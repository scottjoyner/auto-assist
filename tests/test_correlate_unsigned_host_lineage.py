"""Source-only regression tests: correlate two unsigned physical observations."""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "correlate_unsigned_host_lineage",
    ROOT / "scripts" / "correlate_unsigned_host_lineage.py",
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
NOW = 1_800_000_000_000
BOOT = "d867483a-d299-49d0-ac3a-7fa4a7d41c1a"
SHA = "c" * 64


def proof():
    p = {
        "schema": "linux-gguf-process-continuity-v1",
        "source_kind": "unsigned_host_local_proc",
        "observed_at_ms": NOW - 15_000,
        "pid": 4783, "boot_id": BOOT, "process_start_ticks": 2340,
        "model_artifact_fingerprint": "sha256:" + SHA,
        "model_bytes": 1246255456,
        "continuity_checked": True, "signature_verified": False,
        "model_loaded_attested": False, "owner_bound": False,
        "can_approve": False, "can_dispatch": False,
    }
    lineage_row = {
        "schema": "lmstudio-worker-lineage-observation-v1",
        "source_kind": "unsigned_host_local_linux_process",
        "observed_at_ms": NOW - 10_000,
        "boot_id": BOOT,
        "gateway_pid": 1535, "gateway_process_start_ticks": 1125,
        "gateway_listener_port": 1234, "worker_pid": 4783,
        "worker_process_start_ticks": 2340, "worker_listener_port": 45435,
        "worker_parent_pid": 1535, "listener_ownership_observed": True,
        "direct_worker_health_ok": True,
        "physical_model_digest_checked_by_this_tool": False,
        "model_loaded_inference_attested": False,
        "gateway_forwarding_attested": False,
        "source_signature_verified": False, "operator_approval_present": False,
        "owner_bound_request_attested": False, "can_approve": False,
        "can_dispatch": False,
    }
    return p, lineage_row


class TestUnsignedHostCorrelation(unittest.TestCase):
    def check(self, **changes):
        p, lineage_row = proof()
        params = {
            "physical": p, "lineage": lineage_row,
            "expected_sha256": SHA, "expected_bytes": 1246255456,
            "expected_gateway_port": 1234, "expected_worker_port": 45435,
            "now_ms": NOW,
        }
        params.update(changes)
        return M.correlate(**params)

    def test_pinned_same_boot_process_model_and_sockets(self):
        r = self.check()
        self.assertEqual(r["status"], "CORRELATED_LOCAL_PROCESS_AND_SOCKET_ONLY")
        self.assertEqual(r["gateway_pid"], 1535)
        self.assertEqual(r["worker_pid"], 4783)
        self.assertEqual(r["worker_start_ticks"], 2340)
        self.assertEqual(r["model_sha256"], SHA)

    def test_correlated_result_grants_no_authority(self):
        r = self.check()
        for flag in (
            "source_attested", "model_loaded_execution_attested",
            "gateway_request_forwarding_attested", "authenticated_owner_receipt",
            "operator_approval_present", "can_approve", "can_auto_renew",
            "can_dispatch",
        ):
            self.assertFalse(r[flag])
        self.assertNotIn(BOOT, json.dumps(r))

    def test_different_linux_boot_refused(self):
        p, lineage_row = proof()
        lineage_row["boot_id"] = "11111111-2222-3333-4444-555555555555"
        with self.assertRaisesRegex(ValueError, "boot"):
            self.check(physical=p, lineage=lineage_row)

    def test_pid_reuse_different_start_ticks_refused(self):
        p, lineage_row = proof()
        lineage_row["worker_process_start_ticks"] = 9999
        with self.assertRaisesRegex(ValueError, "process"):
            self.check(physical=p, lineage=lineage_row)

    def test_different_model_worker_refused(self):
        p, lineage_row = proof()
        lineage_row["worker_pid"] = 1006
        with self.assertRaisesRegex(ValueError, "process"):
            self.check(physical=p, lineage=lineage_row)

    def test_wrong_model_digest_refused(self):
        with self.assertRaisesRegex(ValueError, "GGUF"):
            self.check(expected_sha256="d" * 64)

    def test_wrong_model_file_length_refused(self):
        with self.assertRaisesRegex(ValueError, "GGUF"):
            self.check(expected_bytes=123)

    def test_unrelated_direct_listener_refused(self):
        with self.assertRaisesRegex(ValueError, "port"):
            self.check(expected_worker_port=1235)

    def test_unverified_model_loaded_claim_refused(self):
        p, lineage_row = proof()
        lineage_row["model_loaded_inference_attested"] = True
        with self.assertRaisesRegex(ValueError, "authority"):
            self.check(physical=p, lineage=lineage_row)

    def test_fake_signed_output_refused(self):
        p, lineage_row = proof()
        p["signature_verified"] = True
        with self.assertRaisesRegex(ValueError, "authority"):
            self.check(physical=p, lineage=lineage_row)

    def test_future_observation_refused(self):
        p, lineage_row = proof()
        p["observed_at_ms"] = NOW + 6000
        with self.assertRaisesRegex(ValueError, "future"):
            self.check(physical=p, lineage=lineage_row)

    def test_stale_process_observation_refused(self):
        p, lineage_row = proof()
        p["observed_at_ms"] = NOW - 301_000
        with self.assertRaisesRegex(ValueError, "expired"):
            self.check(physical=p, lineage=lineage_row)

    def test_gap_too_long_refused(self):
        p, lineage_row = proof()
        p["observed_at_ms"] = NOW - 140_000
        with self.assertRaisesRegex(ValueError, "too far"):
            self.check(physical=p, lineage=lineage_row)

    def test_unknown_raw_secret_field_refused(self):
        p, lineage_row = proof()
        lineage_row["api_token"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.check(physical=p, lineage=lineage_row)

    def test_missing_or_false_direct_health_refused(self):
        p, lineage_row = proof()
        lineage_row["direct_worker_health_ok"] = False
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.check(physical=p, lineage=lineage_row)

    def test_two_unsigned_records_do_not_authenticate_one_another(self):
        r = self.check()
        self.assertFalse(r["source_attested"])
        self.assertFalse(r["gateway_request_forwarding_attested"])

    def test_bool_timestamp_fails_closed(self):
        p, lineage_row = proof()
        p["observed_at_ms"] = True
        with self.assertRaisesRegex(ValueError, "physical observation time"):
            self.check(physical=p, lineage=lineage_row)


if __name__ == "__main__":
    unittest.main()
