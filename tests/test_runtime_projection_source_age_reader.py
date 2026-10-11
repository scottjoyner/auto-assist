"""Fail-closed source-observation age checks in signed fleet projection readers.

All model data, clock values and graph rows are invented. No actual Neo4j,
AssistX service, WAN or runtime connection is performed.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "runtime_projection_source_age", ROOT / "src" / "assistx" / "runtime_projection.py"
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
NOW = 1_000_000
SOURCE_HASH = "a" * 64


def fixtures(signed: bool = True):
    state = {
        "generation": 7, "revision": "rev7",
        "status": "approved", "approved_by": "reviewer",
        "approval_id": "approved7", "manifest_checksum": "b" * 64,
        "expires_at_ts": NOW + 900_000,
    }
    if signed:
        state["source_receipt_sha256"] = SOURCE_HASH
    runtime = {
        "runtime_instance_id": "runtime_a", "node_id": "node_a",
        "runtime_kind": "llama_cpp", "runtime_version": "1.2",
        "headless": True, "process_id": "1234",
        "observed_at_ts": NOW - 90_000, "expires_at_ts": NOW + 900_000,
        "loaded_models": [{
            "admitted": True, "model_instance_id": "model_a",
            "model_key": "some_model", "provider_model": "some_model",
            "artifact_fingerprint": "sha256:abcdef",
            "quantization": "Q8_0", "context_length": 8192,
            "capabilities_json": ["chat", "local_only"],
            "observed_at_ts": NOW - 90_000,
            "expires_at_ts": NOW + 900_000,
        }],
    }
    paths = [{
        "runtime_instance_id": "runtime_a",
        "base_url": "http://192.168.1.2:1234/v1" if transport == "lan"
        else "http://100.101.1.2:1234/v1",
        "transport": transport, "preference": rank,
        "observed_at_ts": NOW - 60_000,
        "expires_at_ts": NOW + 900_000,
        "approved_by": "reviewer", "approval_id": "approved7",
    } for rank, transport in enumerate(("lan", "tailscale"))]
    capacity = [{
        "runtime_instance_id": "runtime_a", "parallel_slots": 1,
        "queue_limit": 0, "queue_timeout_seconds": 0,
        "observed_at_ts": NOW - 90_000,
        "expires_at_ts": NOW + 900_000,
        "approved_by": "reviewer", "approval_id": "approved7",
    }]
    return state, runtime, paths, capacity


def render(state, runtime, paths, capacity, now=NOW):
    with patch.object(M, "_projection_state", return_value=state), \
         patch.object(M, "_runtime_rows", return_value=[runtime]), \
         patch.object(M, "_access_rows", return_value=paths), \
         patch.object(M, "_capacity_rows", return_value=capacity):
        return M.build_runtime_projection(
            lambda: None, secret="fixture_only", ttl_seconds=900, now_ms=now
        )


class TestSignedSourceFreshness(unittest.TestCase):
    def test_signed_reader_bounds_projection_to_earliest_source_canary(self):
        d = render(*fixtures())
        self.assertEqual(d["expires_at_ms"], NOW + 120_000)
        self.assertEqual(len(d["providers"]), 1)
        self.assertEqual(len(d["providers"][0]["access_urls"]), 2)

    def test_cannot_reissue_signed_projection_after_private_canary_stales(self):
        with self.assertRaisesRegex(M.RuntimeProjectionBlocked, "signed source evidence stale"):
            render(*fixtures(), now=NOW + 121_000)

    def test_signed_runtime_observed_at_must_exist(self):
        state, runtime, paths, cap = fixtures()
        del runtime["observed_at_ts"]
        with self.assertRaisesRegex(M.RuntimeProjectionBlocked, "physical runtime"):
            render(state, runtime, paths, cap)

    def test_signed_model_observed_at_must_exist(self):
        state, runtime, paths, cap = fixtures()
        del runtime["loaded_models"][0]["observed_at_ts"]
        with self.assertRaisesRegex(M.RuntimeProjectionBlocked, "loaded model"):
            render(state, runtime, paths, cap)

    def test_signed_capacity_observed_at_must_exist(self):
        state, runtime, paths, cap = fixtures()
        del cap[0]["observed_at_ts"]
        with self.assertRaisesRegex(M.RuntimeProjectionBlocked, "runtime capacity"):
            render(state, runtime, paths, cap)

    def test_signed_path_observed_at_must_exist(self):
        state, runtime, paths, cap = fixtures()
        del paths[0]["observed_at_ts"]
        with self.assertRaisesRegex(M.RuntimeProjectionBlocked, "private access path"):
            render(state, runtime, paths, cap)

    def test_signed_future_path_canary_refused(self):
        state, runtime, paths, cap = fixtures()
        paths[0]["observed_at_ts"] = NOW + 7_000
        with self.assertRaisesRegex(M.RuntimeProjectionBlocked, "future-dated"):
            render(state, runtime, paths, cap)

    def test_signed_physical_expiry_before_canonical_expiry(self):
        state, runtime, paths, cap = fixtures()
        for path in paths:
            path["observed_at_ts"] = NOW
        d = render(state, runtime, paths, cap)
        self.assertEqual(d["expires_at_ms"], NOW + 180_000)
        self.assertLess(d["expires_at_ms"], state["expires_at_ts"])

    def test_signed_state_receipt_digest_must_be_valid(self):
        state, runtime, paths, cap = fixtures()
        state["source_receipt_sha256"] = "bad"
        with self.assertRaisesRegex(M.RuntimeProjectionBlocked, "receipt digest"):
            render(state, runtime, paths, cap)

    def test_boolean_timestamp_does_not_count_as_unsigned_int(self):
        state, runtime, paths, cap = fixtures()
        runtime["observed_at_ts"] = True
        with self.assertRaisesRegex(M.RuntimeProjectionBlocked, "physical runtime"):
            render(state, runtime, paths, cap)

    def test_legacy_projections_not_misrepresented_as_source_signed(self):
        state, runtime, paths, cap = fixtures(signed=False)
        for field in ("observed_at_ts",):
            del runtime[field]
            del runtime["loaded_models"][0][field]
            for path in paths:
                del path[field]
            del cap[0][field]
        # Legacy behavior is intentionally preserved on the research branch:
        # this is NOT a signed physical source proof, and its retirement remains
        # a separate explicit migration blocker.
        d = render(state, runtime, paths, cap)
        self.assertEqual(d["expires_at_ms"], state["expires_at_ts"])


if __name__ == "__main__":
    unittest.main()
