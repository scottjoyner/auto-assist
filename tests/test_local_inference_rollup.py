"""Private receipt rollup coverage and fail-closed accounting tests."""
import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts/summarize_local_inference_canaries.py"
SPEC = importlib.util.spec_from_file_location("local_inference_rollup", PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def receipt(name="r1", **changes):
    row = {
        "schema": "assistx-local-inference-canary/v1",
        "node_claim": "test-node",
        "producer": "manual-readonly-canary",
        "request_id": name,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "result": "completed",
        "matched_expected_ready": True,
        "stream_done": True,
        "first_content_ms": 20.5,
        "elapsed_ms": 40.5,
        "gateway_proven": False,
        "backend_counts": {"prompt_tokens": 8, "completion_tokens": 2,
                           "total_tokens": 10, "cached_prompt_tokens": 3},
    }
    row.update(changes)
    return row


def persist(folder, name, row, mode=0o600):
    path = pathlib.Path(folder) / (name + ".json")
    path.write_text(json.dumps(row))
    path.chmod(mode)
    return path


class RollupContracts(unittest.TestCase):
    def test_complete_usage_not_cache_double_count(self):
        with tempfile.TemporaryDirectory() as root:
            persist(root, "one", receipt())
            report = module.rollup(root)
        self.assertEqual(report["backend_usage_complete"], 1)
        self.assertEqual(report["token_counts"]["total_tokens"], 10)
        self.assertEqual(report["token_counts"]["cached_prompt_tokens"], 3)
        self.assertEqual(report["gateway_confirmed_requests"], 0)
        self.assertEqual(report["stream_terminal_passing_requests"], 1)
        self.assertEqual(report["stream_first_content_p50_ms"], 20.5)

    def test_duplicate_not_counted_twice(self):
        with tempfile.TemporaryDirectory() as root:
            persist(root, "one", receipt("repeat"))
            persist(root, "two", receipt("repeat"))
            report = module.rollup(root)
        self.assertEqual(report["observed_requests"], 1)
        self.assertEqual(report["rejected"]["duplicate_request"], 1)
        self.assertEqual(report["token_counts"]["total_tokens"], 10)

    def test_incomplete_usage_remains_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            persist(root, "one", receipt(backend_counts={"prompt_tokens": 10}))
            report = module.rollup(root)
        self.assertEqual(report["backend_usage_unknown"], 1)
        self.assertEqual(report["token_counts"]["total_tokens"], 0)

    def test_forged_total_and_cache_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            persist(root, "one", receipt(
                backend_counts={"prompt_tokens": 8, "completion_tokens": 2,
                                "total_tokens": 900, "cached_prompt_tokens": 9}))
            report = module.rollup(root)
        self.assertEqual(report["backend_usage_inconsistent"], 1)
        self.assertEqual(report["backend_usage_complete"], 0)

    def test_stale_events_dropped(self):
        with tempfile.TemporaryDirectory() as root:
            old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            persist(root, "old", receipt(started_utc=old))
            report = module.rollup(root)
        self.assertEqual(report["observed_requests"], 0)
        self.assertEqual(report["rejected"]["outside_window"], 1)

    def test_unsafe_permissions_and_symlinks_refused(self):
        with tempfile.TemporaryDirectory() as root:
            original = persist(root, "public", receipt(), mode=0o644)
            (pathlib.Path(root) / "link.json").symlink_to(original)
            report = module.rollup(root)
        self.assertEqual(report["observed_requests"], 0)
        self.assertEqual(report["rejected"]["unsafe_file_custody"], 1)
        self.assertEqual(report["rejected"]["symlink"], 1)

    def test_unknown_schema_refused(self):
        with tempfile.TemporaryDirectory() as root:
            persist(root, "bad", receipt(schema="legacy"))
            report = module.rollup(root)
        self.assertEqual(report["rejected"]["unexpected_schema"], 1)

    def test_no_mutation_of_sources(self):
        with tempfile.TemporaryDirectory() as root:
            p = persist(root, "valid", receipt())
            before = (p.read_bytes(), p.stat().st_mtime_ns)
            module.rollup(root)
            self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), before)

    def test_percentiles_no_samples(self):
        self.assertIsNone(module.percentile([], .95))
        self.assertEqual(module.percentile([100], .95), 100)
        self.assertEqual(module.percentile([0, 100], .95), 95.0)


if __name__ == "__main__":
    unittest.main()
