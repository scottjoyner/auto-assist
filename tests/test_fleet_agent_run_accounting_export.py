"""Offline tests of AgentRun projection, using actual FleetExecutor methods."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
from fleet_agent_run_accounting_export import summarize_agent_runs, MAX_RESULT_BYTES, InvalidProjection

tree = ast.parse((ROOT / "src/assistx/fleet_executor.py").read_text())
klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FleetExecutor")
methods = [n for n in klass.body if isinstance(n, ast.FunctionDef) and
           n.name in {"_router_accounting", "_result_from_router"}]
assert len(methods) == 2
fixture = ast.Module(body=[ast.ClassDef(name="FleetExecutor", bases=[], keywords=[],
    body=methods, decorator_list=[], type_params=[])], type_ignores=[])
ns = {"Any": Any}
exec(compile(ast.fix_missing_locations(fixture), "producer", "exec"), ns)
Executor = ns["FleetExecutor"]


class ExportTests(unittest.TestCase):
    def row(self, task="task", claim="claim", run="run", status=200):
        body={"choices":[{"message":{"content":"PRIVATE_FIXTURE"},"finish_reason":"stop"}],
              "usage":{"prompt_tokens":5,"completion_tokens":3}}
        terminal,result=Executor._result_from_router({"status_code":status,"body":body})
        result.update(projection_generation=3,projection_revision="revision",claim_id=claim)
        return {"linked_task_id":task,"task_id":task,"run_id":run,"agent":"fleet-executor",
                "completion_id":"safe-fleet/complete/"+task+"/"+claim,"claim_id":claim,
                "status":terminal,"result_json":json.dumps(result)}
    def report(self,rows):
        return summarize_agent_runs(rows,expected_agent="fleet-executor")
    def test_real_result_and_replay(self):
        a=self.row()
        r=self.report([a,copy.deepcopy(a),self.row(claim="next",run="second",status=503)])
        self.assertEqual(r["decision"],"PROJECTED_ONLY")
        self.assertEqual(r["identical_export_replays"],1)
        self.assertEqual(r["accounting"]["reported_total_all_attempts"],16)
        self.assertIsNone(r["accounting"]["actual_endpoint_calls"])
        self.assertFalse(r["graph_provenance_verified"])
        self.assertNotIn("PRIVATE_FIXTURE",json.dumps(r))
    def test_duplicate_attempt_distinct_runs_hold(self):
        r=self.report([self.row(),self.row(run="other")])
        self.assertEqual(r["lineage_errors"],{"multiple_runs_per_attempt":1})
        self.assertEqual(r["decision"],"HOLD")
        self.assertIsNone(r["accounting"]["reported_total_all_attempts"])
    def test_same_run_changed_payload_holds(self):
        a=self.row();b=copy.deepcopy(a)
        obj=json.loads(b["result_json"]);obj["content"]="changed";b["result_json"]=json.dumps(obj)
        r=self.report([a,b])
        self.assertEqual(r["lineage_errors"],{"conflicting_run_id":1})
        self.assertEqual(r["decision"],"HOLD")
    def test_cross_task_completion_collision(self):
        a=self.row(task="a/b",claim="c");b=self.row(task="a",claim="b/c",run="next")
        self.assertEqual(a["completion_id"],b["completion_id"])
        r=self.report([a,b]);self.assertEqual(r["lineage_errors"],{"multiple_runs_per_completion":1})
        self.assertIsNone(r["accounting"]["reported_total_complete_subset"])
    def test_relationship_agent_and_completion_lineage(self):
        for k,v,reason in (("linked_task_id","other","task_relationship_mismatch"),
                           ("agent","other","unexpected_agent"),
                           ("completion_id","bad","completion_lineage_mismatch"),
                           ("status","RUNNING","unexpected_terminal_status")):
            with self.subTest(k=k):
                a=self.row();a[k]=v
                self.assertEqual(self.report([a])["lineage_errors"],{reason:1})
    def test_result_claim_and_terminal_mismatch(self):
        for k,v,reason in (("claim_id","wrong","result_claim_mismatch"),
                           ("exit_code",True,"terminal_result_mismatch"),
                           ("exit_code",1,"terminal_result_mismatch")):
            a=self.row();obj=json.loads(a["result_json"]);obj[k]=v;a["result_json"]=json.dumps(obj)
            self.assertEqual(self.report([a])["lineage_errors"],{reason:1})
    def test_historical_and_exception_results_are_unaccounted(self):
        a=self.row();obj=json.loads(a["result_json"]);del obj["response_accounting"];a["result_json"]=json.dumps(obj)
        b=self.row(run="b",claim="b",status=503);b["completion_id"]="safe-fleet/failure/task/b";b["result_json"]='{"exit_code":1,"error":"PRIVATE_FIXTURE"}'
        r=self.report([a,b,self.row(run="c",claim="c")])
        self.assertEqual(r["decision"],"HOLD")
        self.assertEqual(r["unaccounted_unique_runs"],2)
        self.assertIsNone(r["accounting"]["reported_total_complete_subset"])
    def test_json_limits_and_duplicate_keys(self):
        for value,reason in (('{"x":1,"x":2}',"duplicate_result_key"),('{"x":NaN}',"nonfinite_result"),
                             ('{"x":'+'['*65+'0'+']'*65+'}',"result_depth_limit"),
                             (' '* (MAX_RESULT_BYTES+1),"result_size_or_type")):
            a=self.row();a["result_json"]=value
            self.assertEqual(self.report([a])["lineage_errors"],{reason:1})
    def test_aggregate_budget(self):
        a=self.row();obj=json.loads(a["result_json"]);obj["content"]="a"*(MAX_RESULT_BYTES-2048)
        a["result_json"]=json.dumps(obj)
        with self.assertRaisesRegex(InvalidProjection,"batch_byte_limit"):
            self.report([a]*40)
    def test_malformed_accounting_hold(self):
        a=self.row();obj=json.loads(a["result_json"]);obj["response_accounting"]["accepted_work_units"]=1
        a["result_json"]=json.dumps(obj)
        r=self.report([a]);self.assertEqual(r["decision"],"HOLD")
        self.assertEqual(r["accounting"]["malformed_rows"],1)
    def test_empty_and_bad_agent(self):
        self.assertEqual(self.report([])["decision"],"EMPTY")
        with self.assertRaises(InvalidProjection):summarize_agent_runs([],expected_agent="")
if __name__=="__main__":
    unittest.main(verbosity=2)
