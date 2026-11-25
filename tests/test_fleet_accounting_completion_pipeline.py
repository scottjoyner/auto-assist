"""Pinned source-method integration with synthetic graph and router only.

FLEET_NEO_METHOD must contain the exact complete_task source slice from
neo4j_client.py Git blob 7ae80400def477d0527dc33a08702389b4d8f5dc.
No imports of the live executor or Neo4j client; no Cypher engine simulation.
"""
import ast
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import textwrap
import threading
from types import SimpleNamespace
import unittest
import uuid
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))
from fleet_agent_run_accounting_export import summarize_agent_runs

NEO_METHOD_SHA256 = "40ca57bbb47f5d7074513a43ed6ec57c63ccc30dd19a9ff15f1a31d455ec7832"

def build_class(name, methods, namespace):
    cls = ast.ClassDef(name=name, bases=[], keywords=[], body=methods,
                       decorator_list=[], type_params=[])
    tree = ast.Module(body=[cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(tree), name, "exec"), namespace)
    return namespace[name]

def load_neo():
    raw = Path(os.environ["FLEET_NEO_METHOD"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != NEO_METHOD_SHA256:
        raise RuntimeError("Neo4j method digest mismatch")
    methods = ast.parse(textwrap.dedent(raw.decode())).body
    return build_class("Neo4jClient", methods, {"Any":Any,"uuid":uuid,"json":json,
                       "TERMINAL_TASK_STATUSES":{"DONE","FAILED","CANCELLED"}})

class Result:
    def __init__(self, value):
        self.value = value
    def single(self):
        return self.value

class GraphFixture:
    def __init__(self):
        self.runs = []
        self.task = None
        self.reject = False
        self.queries = []
        self.closed = False
        self.client = load_neo()()
        self.client._session = lambda: self
        self.client.close = self.close
    def close(self):
        self.closed = True
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def run(self, query, params):
        self.queries.append((query, dict(params)))
        if "CREATE (r:AgentRun" in query:
            if self.reject:
                return Result(None)
            self.task = {"id":params["task_id"],"status":params["status"],
                         "completed_by":params["agent_id"],"claim_id":params["claim_id"],
                         "result_json":params["result_json"]}
            self.runs.append({"linked_task_id":params["task_id"],"task_id":params["task_id"],
                              "run_id":params["run_id"],"agent":params["agent_id"],
                              "completion_id":params["completion_id"],"claim_id":params["claim_id"],
                              "status":params["status"],"result_json":params["result_json"]})
            return Result({"t":self.task,"run_id":params["run_id"]})
        if "CREATE (m:MemoryItem" in query:
            return Result({"id":params["memory_id"]})
        if "r:AgentRun {completion_id:" in query:
            if self.task and self.runs and all(
                self.runs[-1][k] == params[p] for k,p in (
                    ("task_id","task_id"),("completion_id","completion_id"),
                    ("claim_id","claim_id"),("status","status"),("agent","agent_id"))):
                return Result({"t":self.task,"run_id":self.runs[-1]["run_id"]})
            return Result(None)
        raise AssertionError("unexpected query")

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.graph = GraphFixture()
        self.calls = []
        self.gates = iter([True, True])
        def permit(*args):
            return SimpleNamespace(allowed=next(self.gates,False), reason="fixture")
        tree = ast.parse((ROOT/"src/assistx/fleet_executor.py").read_text())
        cls = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="FleetExecutor")
        methods = [n for n in cls.body if isinstance(n,ast.FunctionDef) and
                   n.name in {"_execute_claimed","_router_accounting","_result_from_router"}]
        self.assertEqual(len(methods),3)
        self.executor = build_class("FleetExecutor",methods,
            {"Any":Any,"ProjectionInventory":Any,"threading":threading,
             "current_supply_permits_effect":permit,"TASK_WALL_TIMEOUT":5,
             "HEARTBEAT_INTERVAL":1,"TASK_LEASE_SECONDS":60,
             "EXECUTOR_AGENT_ID":"fleet-executor","logger":logging.getLogger("fixture")})()
        self.executor.neo_factory = lambda: self.graph.client
        self.executor._request_payload = lambda *args: {"model":"fixture"}
        self.executor.router_runner = self.router
        self.executor._lock = threading.Lock()
        self.executor._active = {"t":True}
        self.executor._futures = {"t":True}
        self.response = {"status_code":200,"body":{"choices":[{"message":{"content":"fixture"},
            "finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3}}}
        self.projection = SimpleNamespace(generation=3,revision="rev")
    def router(self,*args):
        self.calls.append(1)
        return self.response
    def execute(self):
        self.executor._execute_claimed({"id":"t"},"c",self.projection)
        self.assertTrue(self.graph.closed)
        self.assertEqual(self.executor._active,{})
        self.assertEqual(self.executor._futures,{})
    def report(self,rows=None):
        return summarize_agent_runs(self.graph.runs if rows is None else rows,
                                    expected_agent="fleet-executor")
    def test_actual_execution_serialization_to_export(self):
        self.execute()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.graph.runs),1)
        r=self.report()
        self.assertEqual(r["decision"],"PROJECTED_ONLY")
        self.assertEqual(r["accounting"]["reported_total_all_attempts"],8)
        self.assertIsNone(r["accounting"]["actual_endpoint_calls"])
        self.assertIsNone(r["accounting"]["independently_accepted_work_units"])
        self.assertEqual(json.loads(self.graph.task["result_json"])["claim_id"],"c")
    def test_failed_http_accounted_not_productive(self):
        self.response={"status_code":503,"body":{"usage":{"prompt_tokens":5,"completion_tokens":0}}}
        self.execute()
        self.assertEqual(self.graph.runs[0]["status"],"FAILED")
        r=self.report()
        self.assertEqual(r["accounting"]["response_states_nonconflicting"],{"HTTP_NOT_SUCCESS":1})
        self.assertIsNone(r["accounting"]["independently_accepted_work_units"])
    def test_effect_gate_blocks_mock_request(self):
        self.gates=iter([False])
        self.execute()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.graph.queries,[])
        self.assertEqual(self.report()["decision"],"EMPTY")
    def test_revoked_after_response_not_persisted(self):
        self.gates=iter([True,False])
        self.execute()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.graph.queries,[])
        self.assertEqual(self.report()["decision"],"EMPTY")
    def test_rejected_completion_not_exportable(self):
        self.graph.reject=True
        self.execute()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.graph.runs,[])
        self.assertEqual(self.report()["decision"],"EMPTY")
    def test_existing_completion_replay_keeps_original_evidence(self):
        self.execute()
        original=self.graph.runs[0]
        result=json.loads(original["result_json"])
        result["content"]="different retry"
        prior=self.graph.client.complete_task("t","fleet-executor","DONE",
            result=result,idempotency_key="safe-fleet/complete/t/c",claim_id="c")
        self.assertTrue(prior["idempotent"])
        self.assertEqual(len(self.graph.runs),1)
        self.assertNotEqual(json.loads(prior["result_json"])["content"],result["content"])
        r=self.report([original,dict(original)])
        self.assertEqual(r["identical_export_replays"],1)
        self.assertEqual(r["accounting"]["reported_total_all_attempts"],8)
    def test_inconsistent_run_export_blocks_totals(self):
        self.execute()
        row=dict(self.graph.runs[0]);row["run_id"]="different-run"
        r=self.report([self.graph.runs[0],row])
        self.assertEqual(r["decision"],"HOLD")
        self.assertIsNone(r["accounting"]["reported_total_all_attempts"])

if __name__=="__main__":
    unittest.main(verbosity=2)
