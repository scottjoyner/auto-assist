"""Focused Hermes claim-admission regression; no live Neo4j or network."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import patch
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "src/assistx/neo4j_client.py"

def load():
    pkg=types.ModuleType("assistx")
    pkg.__path__=[]
    fake=types.ModuleType("assistx.auto_assign_client")
    fake.notify_task_created=lambda *a,**kw:None
    spec=importlib.util.spec_from_file_location("assistx.neo4j_client",SOURCE)
    loaded=importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules,{
        "assistx":pkg, "assistx.auto_assign_client":fake,
        "assistx.neo4j_client":loaded,
    }):
        spec.loader.exec_module(loaded)
    return loaded.Neo4jClient

class Result:
    def __init__(self,row):self.row=row
    def single(self):return self.row

class Session:
    def __init__(self,rows=()):
        self.rows=list(rows)
        self.queries=[]
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def run(self,sql,params):
        self.queries.append((sql,params))
        return Result(self.rows.pop(0) if self.rows else None)

class ClaimAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Neo=load()

    def client(self,rows=()):
        obj=object.__new__(self.Neo)
        sess=Session(rows)
        obj._session=lambda:sess
        return obj,sess

    def test_hermes_claim_requires_session_before_db_use(self):
        for session in (None,"","  "):
            with self.subTest(session=session):
                cli,db=self.client()
                result=cli.claim_task("synthetic","hermes-local",session_id=session)
                self.assertFalse(result["claimed"])
                self.assertEqual(db.queries,[])

    def test_blank_idempotency_claim_rejected(self):
        for claim in ("","  "):
            cli,db=self.client()
            out=cli.claim_task("synthetic","hermes-local",
                               session_id="s",idempotency_key=claim)
            self.assertFalse(out["claimed"])
            self.assertEqual(db.queries,[])

    def test_idempotent_hermes_requires_current_owner_and_lease(self):
        cli,db=self.client([{"t":{"id":"synthetic","status":"CLAIMED"}}])
        out=cli.claim_task("synthetic","hermes-local",
                           session_id="s",idempotency_key="c")
        self.assertTrue(out["claimed"])
        self.assertTrue(out["idempotent"])
        sql,p=db.queries[0]
        for part in (
            "SET t.hermes_fence_check_epoch", "WITH t",
            "t.claim_id=$claim_id","t.claimed_by=$agent_id",
            "t.agent_session_id=$session_id",
            "t.status IN ['CLAIMED','RUNNING','PAUSING']",
            "coalesce(t.execution_attempt,0)>0",
            "coalesce(t.lease_expires_at_ts,0)>timestamp()",
        ):
            self.assertIn(part,sql)
        self.assertLess(sql.index("SET t.hermes_fence_check_epoch"),
                        sql.index("WHERE t.claim_id=$claim_id"))
        self.assertEqual(p,{"task_id":"synthetic","claim_id":"c",
                            "agent_id":"hermes-local","session_id":"s"})

    def test_inactive_idempotent_hermes_does_not_grant_claim(self):
        cli,db=self.client([None,None,{"status":"DONE",
            "target_agent_id":"hermes-local",
            "required_capabilities":[],"capabilities":[],
            "paperclip_dispatched":False}])
        out=cli.claim_task("synthetic","hermes-local",
                           session_id="s",idempotency_key="old")
        self.assertFalse(out["claimed"])
        self.assertEqual(out["reason"],"not_ready")
        self.assertEqual(len(db.queries),3)

    def test_new_hermes_claim_locks_before_ready_condition(self):
        cli,db=self.client([{"t":{"id":"synthetic","status":"CLAIMED"}}])
        out=cli.claim_task("synthetic","hermes-local",session_id="session")
        self.assertTrue(out["claimed"])
        sql,p=db.queries[0]
        self.assertIn("FOREACH",sql)
        self.assertIn("hermes_fence_check_epoch",sql)
        self.assertLess(sql.index("FOREACH"),sql.index("WHERE t.status='READY'"))
        self.assertEqual(p["agent_id"],"hermes-local")
        self.assertEqual(p["session_id"],"session")
        self.assertTrue(p["claim_id"])

    def test_failed_hermes_claim_remains_denied(self):
        cli,db=self.client([None,{"status":"CANCELLED","target_agent_id":None,
                     "required_capabilities":[],"capabilities":[],
                     "paperclip_dispatched":False}])
        out=cli.claim_task("synthetic","hermes-local",session_id="s")
        self.assertFalse(out["claimed"])
        self.assertEqual(out["status"],"CANCELLED")

    def test_legacy_idempotent_unchanged(self):
        cli,db=self.client([{"t":{"id":"synthetic","status":"DONE"}}])
        out=cli.claim_task("synthetic","legacy-worker",
                           session_id=None,idempotency_key="old")
        self.assertTrue(out["claimed"])
        self.assertTrue(out["idempotent"])
        self.assertEqual(db.queries[0][0],
                         "MATCH (t:Task {id:$task_id, claim_id:$claim_id}) RETURN t")

    def test_legacy_claim_admission_does_not_change_status_gate(self):
        cli,db=self.client([{"t":{"id":"synthetic","status":"CLAIMED"}}])
        cli.claim_task("synthetic","legacy-worker",session_id=None)
        sql,_=db.queries[0]
        self.assertIn("WHEN $agent_id='hermes-local'",sql)
        self.assertIn("WHERE t.status='READY'",sql)

    def test_claim_idempotent_retains_no_production_authority_claim(self):
        cli,db=self.client([{"t":{"id":"synthetic","status":"RUNNING"}}])
        out=cli.claim_task("synthetic","hermes-local",
                           session_id="s",idempotency_key="c")
        self.assertNotIn("production_dispatch_authorized",out)
        self.assertNotIn("independent_custody_verified",out)


if __name__=="__main__":
    unittest.main()
