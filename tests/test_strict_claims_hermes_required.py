"""P0 #428: deny claimless heartbeat/completion for pinned Hermes identity.

Test uses a fake Neo4jClient and never creates a live DB connection.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/"src/assistx/strict_claims.py"


def module():
    spec=importlib.util.spec_from_file_location("hermes_claims_isolated",SCRIPT)
    assert spec and spec.loader
    loaded=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class StrictHermesClaimIdsTests(unittest.TestCase):
    def setUp(self):
        self.c=module()

    def test_hermes_local_claim_id_required_in_default_modern_mode(self):
        with patch.dict("os.environ",{},clear=True):
            self.assertTrue(self.c._claim_required("hermes-local"))
            self.assertTrue(self.c._claim_required("fleet-executor"))
            self.assertFalse(self.c._claim_required("unrelated-legacy-agent"))

    def test_hermes_required_even_if_global_mode_disabled(self):
        for mode in ("0","false","no","off","disabled"):
            with self.subTest(mode=mode):
                with patch.dict("os.environ",
                                {"ASSISTX_REQUIRE_WORKER_CLAIM_ID":mode},
                                clear=True):
                    self.assertTrue(self.c._claim_required("hermes-local"))
                    self.assertFalse(self.c._claim_required("fleet-executor"))

    def test_hermes_required_even_if_legacy_allowlisted(self):
        with patch.dict("os.environ",{
            "ASSISTX_LEGACY_CLAIMLESS_AGENTS":"hermes-local,fleet-executor",
            "ASSISTX_REQUIRE_WORKER_CLAIM_ID":"off"},clear=True):
            self.assertTrue(self.c._claim_required("hermes-local"))
            self.assertFalse(self.c._claim_required("fleet-executor"))

    def test_hermes_required_even_if_required_agents_set_to_other_name(self):
        with patch.dict("os.environ",{
            "ASSISTX_CLAIM_REQUIRED_AGENTS":"some-other-agent",
            "ASSISTX_REQUIRE_WORKER_CLAIM_ID":"modern"},clear=True):
            self.assertTrue(self.c._claim_required("hermes-local"))

    def test_strict_all_mode_for_unrelated_agent_unchanged(self):
        with patch.dict("os.environ",{
            "ASSISTX_REQUIRE_WORKER_CLAIM_ID":"strict"},clear=True):
            self.assertTrue(self.c._claim_required("unrelated-agent"))

    def test_explicit_legacy_exemption_for_nonhermes_remains(self):
        with patch.dict("os.environ",{
            "ASSISTX_REQUIRE_WORKER_CLAIM_ID":"all",
            "ASSISTX_LEGACY_CLAIMLESS_AGENTS":"unrelated-agent"},clear=True):
            self.assertFalse(self.c._claim_required("unrelated-agent"))
            self.assertTrue(self.c._claim_required("hermes-local"))

    def test_user_renamed_identity_must_be_separately_qualified(self):
        with patch.dict("os.environ",{},clear=True):
            self.assertFalse(self.c._claim_required("renamed-custom-hermes"))
            self.assertNotIn("renamed-custom-hermes",
                             self.c._MANDATORY_CLAIM_AGENT_IDS)

    def test_prefixed_hermes_agents_are_mandatory_even_when_legacy_exempt(self):
        with patch.dict("os.environ", {
                "ASSISTX_REQUIRE_WORKER_CLAIM_ID": "off",
                "ASSISTX_LEGACY_CLAIMLESS_AGENTS": "hermes-worker,hermes-synthetic",
        }, clear=True):
            self.assertTrue(self.c._claim_required("hermes-worker"))
            self.assertTrue(self.c._claim_required("hermes-synthetic"))
            self.assertFalse(self.c._claim_required("ordinary-worker"))

    def test_no_secrets_or_external_dependencies_in_mandatory_guard(self):
        self.assertEqual(self.c._MANDATORY_CLAIM_AGENT_IDS,
                         frozenset({"hermes-local"}))

    def test_wrapped_heartbeat_and_complete_reject_omitted_claim_id(self):
        calls=[]
        class FakeNeo4jClient:
            def heartbeat_task(self,task_id,agent_id,**kwargs):
                calls.append(("heartbeat",task_id,agent_id,kwargs))
                return {"status":"RUNNING"}
            def complete_task(self,task_id,agent_id,status,**kwargs):
                calls.append(("complete",task_id,agent_id,kwargs))
                return {"status":status}
        pkg=types.ModuleType("assistx")
        pkg.__path__=[]
        fake_mod=types.ModuleType("assistx.neo4j_client")
        fake_mod.Neo4jClient=FakeNeo4jClient
        spec=importlib.util.spec_from_file_location("assistx.strict_claims",SCRIPT)
        guarded=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{
            "assistx":pkg,
            "assistx.neo4j_client":fake_mod,
            "assistx.strict_claims":guarded,
        }):
            assert spec and spec.loader
            spec.loader.exec_module(guarded)
            guarded.install_strict_claim_fencing()
            db=FakeNeo4jClient()
            with patch.dict("os.environ",{
                "ASSISTX_REQUIRE_WORKER_CLAIM_ID":"off",
                "ASSISTX_LEGACY_CLAIMLESS_AGENTS":"hermes-local"},clear=True):
                self.assertIsNone(db.heartbeat_task(
                    "synthetic-task","hermes-local",claim_id=None))
                self.assertIsNone(db.complete_task(
                    "synthetic-task","hermes-local","DONE",claim_id=""))
                self.assertEqual(calls,[])
                self.assertEqual(db.heartbeat_task(
                    "synthetic-task","hermes-local",claim_id="synthetic-claim"),
                    {"status":"RUNNING"})
                self.assertEqual(db.complete_task(
                    "synthetic-task","hermes-local","DONE",
                    claim_id="synthetic-claim"),{"status":"DONE"})
                self.assertEqual(len(calls),2)

    def test_double_install_does_not_double_wrap_methods(self):
        class FakeNeo4jClient:
            def heartbeat_task(self,*args,**kwargs):
                return True
            def complete_task(self,*args,**kwargs):
                return True
        fake_pkg=types.ModuleType("assistx")
        fake_pkg.__path__=[]
        fake_db=types.ModuleType("assistx.neo4j_client")
        fake_db.Neo4jClient=FakeNeo4jClient
        spec=importlib.util.spec_from_file_location("assistx.strict_claims",SCRIPT)
        loaded=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{
            "assistx":fake_pkg,"assistx.neo4j_client":fake_db,
            "assistx.strict_claims":loaded}):
            spec.loader.exec_module(loaded)
            loaded.install_strict_claim_fencing()
            saved=FakeNeo4jClient.heartbeat_task
            loaded.install_strict_claim_fencing()
            self.assertIs(FakeNeo4jClient.heartbeat_task,saved)


if __name__=="__main__":
    unittest.main()
