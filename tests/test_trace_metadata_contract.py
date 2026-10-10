import importlib.util
import unittest
from pathlib import Path

source = Path(__file__).resolve().parents[1] / "scripts" / "trace_metadata_contract.py"
spec = importlib.util.spec_from_file_location("trace_contract", source)
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


def event(suffix, *, node="x1-370", parent=None, trace_id="trace-1"):
    result = {
        "schema": trace.SCHEMA, "event_id": "event-"+suffix,
        "trace_id": trace_id, "span_id": "span-"+suffix,
        "session_id": "session-1", "node_id": node,
        "producer": "assistx", "operation": "tool",
        "status": "ok", "occurred_at": "2026-10-10T16:00:00Z",
        "sequence": 1,
    }
    if parent is not None:
        result["parent_span_id"] = parent
    return result


class TraceMetadataContractTests(unittest.TestCase):
    def test_cross_node_parent_resolution_requires_same_trace(self):
        root = event("root")
        child = event("child", node="xwing", parent="span-root")
        report = trace.inspect_lineage([root, child])
        self.assertEqual(report["distinct_nodes"], 2)
        self.assertEqual(report["missing_parent_spans"], 0)
        self.assertFalse(report["complete_fleet_coverage"])
        self.assertFalse(report["source_authentication_verified"])
        other = event("other", parent="span-root", trace_id="trace-2")
        self.assertEqual(trace.inspect_lineage([root, other])["missing_parent_spans"], 1)

    def test_idempotent_replay_and_conflicting_event_rejected(self):
        original = event("one")
        self.assertEqual(trace.inspect_lineage([original, dict(original)])["duplicate_events"], 1)
        conflict = dict(original, node_id="other-node")
        with self.assertRaisesRegex(ValueError, "conflicting"):
            trace.inspect_lineage([original, conflict])

    def test_rejects_raw_payload_missing_identity_and_bad_timestamp(self):
        for changed in [
            dict(event("x"), raw_prompt="do not send"),
            dict(event("x"), payload={"coordinates":[1,2]}),
            dict(event("x"), node_id=None),
            dict(event("x"), occurred_at="not-utc"),
            dict(event("x"), status="granted-by-agent"),
        ]:
            with self.assertRaises(ValueError):
                trace.validate_envelope(changed)

    def test_sha_and_sequence_are_strict(self):
        with self.assertRaises(ValueError):
            trace.validate_envelope(dict(event("x"), artifact_sha256="unknown"))
        with self.assertRaises(ValueError):
            trace.validate_envelope(dict(event("x"), sequence=True))
        self.assertEqual(trace.validate_envelope(event("ok"))["span_id"], "span-ok")


if __name__ == "__main__":
    unittest.main()
