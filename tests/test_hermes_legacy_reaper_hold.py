"""P0 #427: source-side mutation quarantine must deny Hermes legacy recovery."""
from __future__ import annotations
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]/"src/assistx"
GUARDS=(
    "coalesce(t.claimed_by, '') <> 'hermes-local'",
    "coalesce(t.target_agent_id, '') <> 'hermes-local'",
    "coalesce(t.hermes_claim_scope, false) = false",
)


def loaded_helpers():
    module=ast.parse((ROOT/"maintenance.py").read_text())
    names={"_fenced_stale_claim_reap","_fenced_terminal_task_purge"}
    fns=[n for n in module.body if isinstance(n,ast.FunctionDef) and n.name in names]
    guards=[n for n in module.body if isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=="_LEGACY_SCOPE_GUARD" for t in n.targets)]
    assert len(fns)==2 and len(guards)==1
    ns={"STALE_CLAIM_REAP_SECONDS":120,"TASK_RETENTION_DAYS":1}
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=guards+fns,type_ignores=[])),str(ROOT/"maintenance.py"),"exec"),ns)
    return ns


class FakeResult:
    def __init__(self,result): self.result=result
    def single(self): return self.result

class FakeTx:
    def __init__(self,records):
        self.records=iter(records)
        self.calls=[]
        self.commits=0
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def run(self,cypher,params):
        self.calls.append((cypher,params))
        return FakeResult(next(self.records))
    def commit(self):self.commits+=1

class FakeSession:
    def __init__(self,results):self.tx=FakeTx(results);self.started=0
    def begin_transaction(self):self.started+=1;return self.tx


class LegacyMutationQuarantineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.helpers=loaded_helpers()

    def test_hermes_marker_is_sticky_when_claimed(self):
        source=(ROOT/"neo4j_client.py").read_text()
        fragment=source.split("    def claim_task(",1)[1].split(
            "    def heartbeat_task(",1)[0]
        self.assertIn("t.hermes_claim_scope=CASE WHEN $agent_id='hermes-local'",fragment)
        self.assertIn("coalesce(t.hermes_claim_scope, false)",fragment)

    def test_reaper_checks_current_owner_in_second_database_statement(self):
        s=FakeSession([{"ids":["synthetic-task"]},{"reaped":1}])
        self.assertEqual(self.helpers["_fenced_stale_claim_reap"](s),1)
        self.assertEqual(s.started,1)
        self.assertEqual(s.tx.commits,1)
        self.assertEqual(len(s.tx.calls),2)
        select,params=s.tx.calls[0]
        commit,post=s.tx.calls[1]
        self.assertIn("hermes_fence_check_epoch",select)
        self.assertIn("RETURN collect(t.id)",select)
        self.assertIn("UNWIND $ids AS id",commit)
        self.assertIn("MATCH (t:Task{id:id})",commit)
        self.assertIn("SET t.status='READY'",commit)
        self.assertEqual(post["ids"],["synthetic-task"])
        for guard in GUARDS:
            self.assertIn(guard,select)
            self.assertIn(guard,commit)

    def test_reaper_no_candidates_has_no_mutation_statement(self):
        s=FakeSession([{"ids":[]}])
        self.assertEqual(self.helpers["_fenced_stale_claim_reap"](s),0)
        self.assertEqual(len(s.tx.calls),1)
        self.assertEqual(s.tx.commits,1)

    def test_reaper_missing_task_identifier_fails_closed(self):
        for ids in ([None],[""],["ok",False],"broken",None):
            with self.subTest(ids=ids):
                s=FakeSession([{"ids":ids}])
                with self.assertRaises(ValueError):
                    self.helpers["_fenced_stale_claim_reap"](s)
                self.assertEqual(s.tx.commits,0)
                self.assertEqual(len(s.tx.calls),1)

    def test_terminal_purge_rechecks_owner_after_lock(self):
        s=FakeSession([{"ids":["synthetic-terminal"]},{"deleted":1}])
        self.assertEqual(self.helpers["_fenced_terminal_task_purge"](s),1)
        self.assertEqual(len(s.tx.calls),2)
        before,params=s.tx.calls[0]
        after,after_params=s.tx.calls[1]
        self.assertIn("hermes_fence_check_epoch",before)
        self.assertIn("UNWIND $ids AS id",after)
        self.assertIn("DETACH DELETE t",after)
        self.assertEqual(after_params["ids"],["synthetic-terminal"])
        for guard in GUARDS:
            self.assertIn(guard,before)
            self.assertIn(guard,after)

    def test_terminal_purge_skips_empty(self):
        s=FakeSession([{"ids":[]}])
        self.assertEqual(self.helpers["_fenced_terminal_task_purge"](s),0)
        self.assertEqual(len(s.tx.calls),1)
        self.assertEqual(s.tx.commits,1)

    def test_terminal_purge_missing_id_does_not_delete(self):
        s=FakeSession([{"ids":[None]}])
        with self.assertRaises(ValueError):
            self.helpers["_fenced_terminal_task_purge"](s)
        self.assertEqual(len(s.tx.calls),1)
        self.assertEqual(s.tx.commits,0)

    def test_both_maintenance_entrypoints_use_fenced_reaper(self):
        source=(ROOT/"maintenance.py").read_text()
        self.assertEqual(source.count("_fenced_stale_claim_reap(s)"),2)
        self.assertEqual(source.count("_fenced_terminal_task_purge(s)"),1)

    def test_reconciler_excludes_hermes_provenance(self):
        source=(ROOT/"execution_control.py").read_text()
        for guard in GUARDS:self.assertGreaterEqual(source.count(guard),3)
        self.assertIn("action:'preemption_timed_out'",source)
        self.assertIn("action:'migration_scheduled'",source)

    def test_legacy_nonhermes_recovery_is_still_available(self):
        s=FakeSession([{"ids":["non-hermes"]},{"reaped":1}])
        self.assertEqual(self.helpers["_fenced_stale_claim_reap"](s),1)
        self.assertIn("CLAIMED_PASSIVE",s.tx.calls[0][0])

    def test_reaper_does_not_return_production_authority(self):
        s=FakeSession([{"ids":[]}])
        self.assertEqual(type(self.helpers["_fenced_stale_claim_reap"](s)),int)
        self.assertNotIn("production_deployment_authorized = True",
                         (ROOT/"maintenance.py").read_text())

if __name__=="__main__":
    unittest.main()
