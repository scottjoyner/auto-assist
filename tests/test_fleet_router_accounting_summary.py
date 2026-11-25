"""Test bounded accounting aggregation using the actual producer methods."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
from fleet_router_accounting_summary import summarize_projections, InvalidProjection, MAX_ROWS

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


class SummaryTests(unittest.TestCase):
    def row(self, task="task", claim="claim", usage=None, finish="stop", status=200):
        body={"choices":[{"message":{"content":"fixture"},"finish_reason":finish}]}
        if usage is not None:
            body["usage"]=usage
        _,r=Executor._result_from_router({"status_code":status,"body":body})
        return {"task_id":task,"claim_id":claim,"projection_generation":1,
                "projection_revision":"rev","response_accounting":r["response_accounting"]}

    def complete(self, **kwargs):
        return self.row(usage={"prompt_tokens":5,"completion_tokens":3},**kwargs)

    def test_actual_producer_complete_and_partial(self):
        rows=[self.complete(),self.row(claim="b",usage={"completion_tokens":4}),
              self.row(claim="c",finish="length"),self.row(claim="d",status=503)]
        r=summarize_projections(rows)
        self.assertEqual(r["decision"],"PROJECTED_ONLY")
        self.assertEqual(r["distinct_attempt_keys"],4)
        self.assertEqual(r["reported_total_complete_subset"],8)
        self.assertIsNone(r["reported_total_all_attempts"])
        self.assertEqual(r["response_states_nonconflicting"]["TEXT_TRUNCATED_REPORTED"],1)
        self.assertIsNone(r["actual_endpoint_calls"])
        self.assertIsNone(r["independently_accepted_work_units"])
        self.assertIsNone(r["verified_endpoint_receipts"])

    def test_identical_projection_replay_no_double_count(self):
        a=self.complete()
        r=summarize_projections([a,copy.deepcopy(a),self.complete(claim="next")])
        self.assertEqual(r["identical_projection_replay_rows"],1)
        self.assertEqual(r["reported_total_all_attempts"],16)

    def test_conflict_never_sums_evidence(self):
        a=self.complete()
        b=self.row(usage={"prompt_tokens":5,"completion_tokens":4})
        for rows in ([a,b,self.complete(claim="other")],[b,a,self.complete(claim="other")]):
            r=summarize_projections(rows)
            self.assertEqual(r["decision"],"HOLD")
            self.assertEqual(r["conflicting_attempt_keys"],1)
            self.assertIsNone(r["reported_total_complete_subset"])
            self.assertIsNone(r["reported_total_all_attempts"])

    def test_generation_or_revision_change_is_conflict(self):
        for k,v in (("projection_generation",2),("projection_revision","changed")):
            a=self.complete()
            b=copy.deepcopy(a);b[k]=v
            self.assertEqual(summarize_projections([a,b])["conflicting_attempt_keys"],1)

    def test_malformed_and_authority_claims_hold_entire_batch(self):
        for k,v in (("provider_execution_verified",True),("accepted_work_units",1),
                    ("independent_quality_verified",0),("billing_verified",True),
                    ("reported_prompt_tokens",True),("reconciled_reported_total_tokens",9),
                    ("usage_state","COMPLETE"),("response_state",[]),("issues",["unknown"])):
            a=self.complete(claim="bad")
            a["response_accounting"][k]=v
            r=summarize_projections([self.complete(),a])
            self.assertEqual(r["decision"],"HOLD")
            self.assertEqual(r["malformed_rows"],1)
            self.assertIsNone(r["reported_total_complete_subset"])

    def test_empty_unknown_invalid_and_measured_zero(self):
        self.assertEqual(summarize_projections([])["decision"],"EMPTY")
        self.assertIsNone(summarize_projections([])["reported_total_all_attempts"])
        for row in (self.row(), self.row(usage={"prompt_tokens":True}),
                    self.row(usage={"total_tokens":8})):
            self.assertIsNone(summarize_projections([row])["reported_total_all_attempts"])
        zero=self.row(usage={"prompt_tokens":0,"completion_tokens":0})
        self.assertEqual(summarize_projections([zero])["reported_total_all_attempts"],0)

    def test_no_raw_identifiers_in_summary(self):
        r=summarize_projections([self.complete(task="PRIVATE_TASK",claim="PRIVATE_CLAIM")])
        self.assertNotIn("PRIVATE_",json.dumps(r))
        self.assertFalse(r["dispatch_authorized"])
        self.assertFalse(r["source_custody_verified"])

    def test_batch_and_lineage_limits(self):
        for rows in (None,tuple(),[None]*(MAX_ROWS+1)):
            with self.assertRaises(InvalidProjection):
                summarize_projections(rows)
        for key,val in (("task_id",""),("claim_id","x"*513),("projection_generation",True)):
            row=self.complete();row[key]=val
            self.assertEqual(summarize_projections([row])["decision"],"HOLD")

    def test_order_independence_with_replays_of_conflicts(self):
        a=self.complete();b=self.row(usage={"prompt_tokens":6,"completion_tokens":3})
        rows=[a,b,a,b,self.complete(claim="other")]
        self.assertEqual(summarize_projections(rows),summarize_projections(list(reversed(rows))))
        self.assertEqual(summarize_projections(rows)["identical_projection_replay_rows"],2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
