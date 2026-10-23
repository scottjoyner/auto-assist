"""Hermes claim fence in Neo4j DB source; never connects to a real database."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "src/assistx/neo4j_client.py"


class Result:
    def __init__(self, row):
        self.row = row

    def single(self):
        return self.row


class Session:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *ignored):
        return None

    def run(self, query, params=None):
        self.queries.append((query, params))
        return Result(self.rows.pop(0) if self.rows else None)


def load():
    package = types.ModuleType("assistx")
    package.__path__ = []
    fake = types.ModuleType("assistx.auto_assign_client")
    fake.notify_task_created = lambda *a, **kw: None
    spec = importlib.util.spec_from_file_location("assistx.neo4j_client", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "assistx": package,
        "assistx.auto_assign_client": fake,
        "assistx.neo4j_client": module,
    }):
        assert spec and spec.loader
        spec.loader.exec_module(module)
    return module.Neo4jClient


class HermesDbFencingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Neo4jClient = load()

    def client(self, rows=None):
        session = Session(rows)
        client = object.__new__(self.Neo4jClient)
        client._session = lambda: session
        return client, session

    def test_missing_claim_heartbeat_never_opens_session(self):
        client, session = self.client()
        for value in (None, "", "  "):
            self.assertIsNone(client.heartbeat_task("synthetic", "hermes-local",
                                                     claim_id=value, session_id="s"))
        self.assertEqual(session.queries, [])

    def test_missing_session_heartbeat_never_opens_session(self):
        client, session = self.client()
        for value in (None, "", " "):
            self.assertIsNone(client.heartbeat_task("synthetic", "hermes-local",
                                                     claim_id="c", session_id=value))
        self.assertEqual(session.queries, [])

    def test_missing_claim_completion_never_opens_session(self):
        client, session = self.client()
        for value in (None, "", "  "):
            self.assertIsNone(client.complete_task("synthetic", "hermes-local",
                                                    "DONE", claim_id=value, session_id="s"))
        self.assertEqual(session.queries, [])

    def test_missing_session_completion_never_opens_session(self):
        client, session = self.client()
        for value in (None, "", " "):
            self.assertIsNone(client.complete_task("synthetic", "hermes-local",
                                                    "DONE", claim_id="c", session_id=value))
        self.assertEqual(session.queries, [])

    def test_heartbeat_current_claim_is_fenced_inside_cypher(self):
        client, session = self.client([{"t": {"status": "RUNNING"}}])
        out = client.heartbeat_task("synthetic", "hermes-local",
                                    status="RUNNING", claim_id="c", session_id="s")
        self.assertEqual(out["status"], "RUNNING")
        sql, params = session.queries[0]
        for fragment in (
            "$agent_id STARTS WITH 'hermes-'",
            "t.claimed_by=$agent_id",
            "t.claim_id=$claim_id",
            "t.agent_session_id=$session_id",
            "coalesce(t.execution_attempt, 0)>0",
            "coalesce(t.lease_expires_at_ts, 0)>timestamp()",
            "t.status IN ['CLAIMED','RUNNING','PAUSING']",
        ):
            self.assertIn(fragment, sql)
        self.assertEqual(params["claim_id"], "c")
        self.assertEqual(params["session_id"], "s") if "session_id" in params else None
        # The Cypher must receive the exact session used by the claim check.
        self.assertIn("session_id", params)

    def test_completion_current_claim_is_fenced_inside_cypher(self):
        client, session = self.client([{"t": {"status": "DONE"}, "run_id": "r"}])
        out = client.complete_task("synthetic","hermes-local","DONE",
                                    claim_id="c",session_id="s",summary=None)
        self.assertEqual(out["run_id"],"r")
        sql, params = session.queries[0]
        for fragment in (
            "$agent_id STARTS WITH 'hermes-'",
            "t.claimed_by=$agent_id",
            "t.claim_id=$claim_id",
            "t.agent_session_id=$session_id",
            "coalesce(t.execution_attempt, 0)>0",
            "coalesce(t.lease_expires_at_ts, 0)>timestamp()",
            "t.status IN ['CLAIMED','RUNNING']",
        ):
            self.assertIn(fragment, sql)
        self.assertEqual(params["claim_id"],"c")
        self.assertEqual(params["session_id"],"s")

    def test_old_idempotent_hermes_completion_never_rewrites_terminal_state(self):
        for old_status in ("DONE","FAILED","CANCELLED"):
            with self.subTest(status=old_status):
                client,session=self.client([{
                    "t":{"status":old_status},
                    "run_id":"old-run"
                }])
                out=client.complete_task("synthetic","hermes-local","DONE",
                                         idempotency_key="replayed-transaction",
                                         claim_id="c", session_id="s")
                self.assertIsNone(out)
                self.assertEqual(len(session.queries),1)
                self.assertNotIn(" SET t.status=",session.queries[0][0])

    def test_nonhermes_idempotent_readback_remains(self):
        client, session = self.client([{
            "t":{"status":"DONE"},"run_id":"old-run"
        }])
        out=client.complete_task("synthetic","unrelated-agent","DONE",
                                 idempotency_key="same-key")
        self.assertTrue(out["idempotent"])
        self.assertEqual(out["run_id"],"old-run")
        self.assertEqual(len(session.queries),1)

    def test_nonhermes_legacy_queries_remain_scoped_to_other_agents(self):
        client,session=self.client([{"t":{"status":"RUNNING"}}])
        client.heartbeat_task("synthetic","unrelated-agent",claim_id=None)
        sql,_=session.queries[0]
        self.assertIn("NOT ($agent_id STARTS WITH 'hermes-'",sql)
        self.assertIn("($claim_id IS NULL OR t.claim_id=$claim_id)",sql)
        self.assertIn("coalesce(t.execution_attempt, 0)=0",sql)

    def test_expired_lease_and_attempt_zero_in_hermes_branch_are_disallowed_in_source(self):
        client,session=self.client()
        client.complete_task("synthetic","hermes-local","DONE",
                             claim_id="c", session_id="s")
        sql=session.queries[0][0]
        strict=sql.split("$agent_id STARTS WITH 'hermes-'",1)[1].split(
            "NOT ($agent_id STARTS WITH 'hermes-'",1)[0]
        self.assertNotIn("($claim_id IS NULL",strict)
        self.assertNotIn("execution_attempt, 0)=0",strict)
        self.assertIn("lease_expires_at_ts, 0)>timestamp()",strict)

    def test_completion_does_not_treat_old_agent_run_as_current_claim(self):
        client,session=self.client([{
            "t":{"status":"FAILED"},"run_id":"old"
        }])
        out=client.complete_task("synthetic","hermes-local","DONE",
                                 claim_id="new",session_id="s",
                                 idempotency_key="old")
        self.assertIsNone(out)
        self.assertEqual(len(session.queries),1)


    def test_cancellation_locks_before_rechecking_terminal_state(self):
        client,session=self.client([{"task_id":"synthetic"}])
        self.assertTrue(client.update_task_status("synthetic","CANCELLED"))
        sql,params=session.queries[0]
        self.assertIn("FOREACH",sql)
        self.assertLess(sql.index("hermes_fence_check_epoch"),
                        sql.index("WHERE $st<>'CANCELLED'"))
        self.assertIn("t.status IN ['READY','CLAIMED','RUNNING','PAUSING']",sql)
        self.assertEqual(params,{"id":"synthetic","st":"CANCELLED"})

    def test_cancellation_missing_or_terminal_task_returns_false(self):
        client,session=self.client([None])
        self.assertFalse(client.update_task_status("synthetic","CANCELLED"))
        self.assertEqual(len(session.queries),1)


    def test_prefixed_hermes_identity_is_never_claimless(self):
        client, session = self.client()
        for agent_id in ("hermes-worker", "hermes-executor-v2"):
            self.assertIsNone(client.heartbeat_task("synthetic", agent_id,
                                                    session_id="session", claim_id=None))
            self.assertIsNone(client.complete_task("synthetic", agent_id, "DONE",
                                                   session_id="session", claim_id=None))
        self.assertEqual(session.queries, [])

    def test_hermes_targeted_task_uses_strict_mutation_not_legacy_path(self):
        client, session = self.client([{"t": {"status": "RUNNING"}}])
        client.heartbeat_task("synthetic", "unrelated-agent")
        cypher, _ = session.queries[0]
        self.assertIn("coalesce(t.target_agent_id, '') STARTS WITH 'hermes-'", cypher)
        self.assertIn("coalesce(t.claimed_by, '') STARTS WITH 'hermes-'", cypher)
        self.assertIn("NOT ($agent_id STARTS WITH 'hermes-'", cypher)
        self.assertLess(cypher.index("hermes_fence_check_epoch"),
                        cypher.index("WHERE ("))

    def test_cross_agent_old_completion_cannot_rewrite_hermes_task(self):
        for label in ("target_agent_id", "claimed_by"):
            with self.subTest(label=label):
                client, session = self.client([{
                    "t": {"status": "FAILED", label: "hermes-worker"},
                    "run_id": "historical",
                }])
                self.assertIsNone(client.complete_task(
                    "synthetic", "unrelated-agent", "DONE",
                    idempotency_key="historical-key"
                ))
                self.assertEqual(len(session.queries), 1)
                self.assertNotIn("SET t.status=", session.queries[0][0])


if __name__=="__main__":
    unittest.main()
