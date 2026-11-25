"""Pinned router producer/outbox evidence limitations; no network or live DB.

Run with ROUTER_SOURCE_ROOT pointing to a reviewed copy of the two pinned
source files. Synthetic fixture only. These tests reproduce limitations,
not approval to count provider calls or change router custody policy.
"""
import ast
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest
import uuid

PINS = {
    "src/auto_router/route_events.py": "d79dcb07c93aaa1d69383cf63dd3a7aea81b3fdb",
    "src/auto_router/event_outbox.py": "024bf48c7f2cf5f97965c5018e69711440abb939",
}


def pinned(path):
    raw = (Path(os.environ["ROUTER_SOURCE_ROOT"]) / path).read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != PINS[path]:
        raise RuntimeError("router source pin mismatch")
    return raw.decode("utf-8")


class RouterEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Outbox is pure stdlib; no live service imports. Use actual in-memory DB.
        import types
        import sys
        module = types.ModuleType("fixture_outbox")
        sys.modules[module.__name__] = module
        exec(compile(pinned("src/auto_router/event_outbox.py"), "outbox", "exec"), module.__dict__)
        cls.outbox_class = module.EventOutbox
        names = {"_canonicalizer", "_provider_node_id", "_projection_metadata",
                 "enqueue_route_execution_event", "_provider_scoped_model_id", "_route_status"}
        tree = ast.parse(pinned("src/auto_router/route_events.py"))
        methods = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        if len(methods) != len(names):
            raise RuntimeError("producer extraction mismatch")
        ns = {"Any":Any, "RouterRequest":Any, "uuid":uuid, "OutboxEvent":module.OutboxEvent,
              "ensure_event_outbox":lambda state:state.event_outbox}
        exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])),
                     "producer", "exec"), ns)
        cls.emit = staticmethod(ns["enqueue_route_execution_event"])

    def setUp(self):
        self.outbox = self.outbox_class("sqlite:///:memory:")
        self.addCleanup(lambda:self.outbox._memory_connection.close())
        self.state = SimpleNamespace(event_outbox=self.outbox)
        self.request = SimpleNamespace(request_id="request-A", route="fixture", model="m",
            priority=SimpleNamespace(value="normal"), local_only=True, allow_cloud=False,
            stream=False, task_id="task-A", agent_run_id=None, correlation_id="correlation-A",
            metadata={"claim_id":"claim-A", "runtime_projection_generation":3,
                      "runtime_projection_revision":"rev", "executor":"fleet-executor"})
        self.estimate = SimpleNamespace(input_tokens=17,total_tokens=29,dimensions={})

    def event(self, stage="primary", usage=None, status=200, **kwargs):
        return self.emit(self.state,self.request,"provider","m",stage,self.estimate,
                         status,10,usage=usage,**kwargs)

    def test_claim_lineage_not_emitted(self):
        self.event(usage={"prompt_tokens":5,"completion_tokens":3,"total_tokens":8})
        p=self.outbox.recent()[0]["payload"]
        self.assertEqual(p["task_id"],"task-A")
        self.assertEqual(p["runtime_projection_generation"],3)
        self.assertNotIn("claim_id",p)
        self.assertIsNone(p["agent_run_id"])

    def test_missing_usage_indistinguishable_from_explicit_zero(self):
        self.event(usage=None)
        self.request.request_id="request-B"
        self.event(usage={"prompt_tokens":0,"completion_tokens":0,"total_tokens":0})
        rows=self.outbox.recent()
        counts=lambda r:tuple(r["payload"][k] for k in ("input_tokens","output_tokens","total_tokens"))
        self.assertEqual(counts(rows[0]),(17,0,29))
        self.assertEqual(counts(rows[1]),counts(rows[0]))

    def test_same_stage_retry_collapses_distinct_usage(self):
        first=self.event(usage={"prompt_tokens":5,"completion_tokens":3,"total_tokens":8})
        second=self.event(usage={"prompt_tokens":6,"completion_tokens":9,"total_tokens":15})
        self.assertEqual(first,second)
        rows=self.outbox.recent()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["payload"]["total_tokens"],8)

    def test_multiple_stages_are_not_multiple_request_ids(self):
        self.event(stage="primary",status=503)
        self.event(stage="fallback",usage={"prompt_tokens":5,"completion_tokens":3,"total_tokens":8})
        rows=self.outbox.recent()
        self.assertEqual(len(rows),2)
        self.assertEqual(len({r["payload"]["request_id"] for r in rows}),1)
        self.assertEqual({r["payload"]["stage"] for r in rows},{"primary","fallback"})

    def test_unknown_status_labeled_completed_without_measurement(self):
        self.event(status=None,tokens_per_second=100.0)
        r=self.outbox.recent()[0]
        self.assertEqual(r["event_type"],"router.execution_stage.completed")
        self.assertIsNone(r["payload"]["status_code"])
        self.assertIsNone(r["payload"]["tokens_per_second"])

    def test_outbox_delivery_state_is_not_execution_outcome(self):
        event=self.event(status=503)
        self.outbox.mark_delivered(event)
        r=self.outbox.recent()[0]
        self.assertEqual(r["status"],"delivered")
        self.assertEqual(r["payload"]["status"],"failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
