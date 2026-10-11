"""No-service, no-Neo4j tests for operator-only renewal review evidence."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "runtime_review_preflight", ROOT / "scripts" / "runtime-projection-review-preflight.py",
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
NOW = datetime(2026, 10, 11, 17, 55, tzinfo=UTC)
HASH = "c40c8a9239dbc2d8874c5508110b9b1f3d325376eecffdba7bce2dcb49a0207c"


def stamp(seconds: int) -> str:
    return (NOW + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid() -> dict:
    return {
        "schema": M.SCHEMA, "kind": "unsigned_read_only_observation",
        "prior_generation": 643, "proposed_generation": 644,
        "previous_approval_expired": True,
        "expected_model_sha256": HASH, "expected_model_bytes": 1246255456,
        "physical": {
            "observed_at_utc": stamp(-30),
            "model_sha256": HASH, "model_bytes": 1246255456,
            "loaded_model_count": 1, "matching_process_count": 1,
            "context_tokens": 8192, "pid": 4783,
            "unsigned_host_source": True,
        },
        "paths": [
            {"transport": name, "observed_at_utc": stamp(-15),
             "http": 200, "exact_marker": True, "max_tokens": 24}
            for name in ("lan", "tailscale")
        ],
        "capacity": {"requested_slots": 1, "physical_process_parallel": 4},
        "operator_review": {
            "new_approval_id": None, "reviewer_id": None,
            "independent_host_signature": False,
        },
    }


class TestReviewPreflight(unittest.TestCase):
    def test_fresh_proof_still_cannot_authorize(self):
        result = M.inspect(valid(), now=NOW)
        self.assertTrue(result["physical_and_path_evidence_fresh"])
        self.assertEqual(result["status"], "REVIEW_BLOCKED")
        self.assertFalse(result["can_apply"])
        self.assertFalse(result["can_dispatch"])
        self.assertFalse(result["can_auto_renew"])
        self.assertFalse(result["source_attested"])
        self.assertIn("NEW_EXPLICIT_OPERATOR_APPROVAL_REQUIRED", result["blockers"])
        self.assertIn("CANONICAL_GENERATION_NOT_INDEPENDENTLY_ATTESTED",
                      result["blockers"])

    def test_stale_physical_after_five_minutes(self):
        sample = valid()
        sample["physical"]["observed_at_utc"] = stamp(-301)
        result = M.inspect(sample, now=NOW)
        self.assertIn("PHYSICAL_STALE", result["blockers"])
        self.assertFalse(result["physical_and_path_evidence_fresh"])

    def test_stale_lan_canary_after_three_minutes(self):
        sample = valid()
        sample["paths"][0]["observed_at_utc"] = stamp(-181)
        self.assertIn("LAN_PATH_STALE", M.inspect(sample, now=NOW)["blockers"])

    def test_stale_tailnet_path_not_interchangeable(self):
        sample = valid()
        sample["paths"][1]["observed_at_utc"] = stamp(-181)
        self.assertIn("TAILSCALE_PATH_STALE", M.inspect(sample, now=NOW)["blockers"])

    def test_path_future_dated_observation_denied(self):
        sample = valid()
        sample["paths"][0]["observed_at_utc"] = stamp(10)
        self.assertIn("LAN_PATH_FUTURE_DATED", M.inspect(sample, now=NOW)["blockers"])

    def test_new_approval_id_in_unsigned_bundle_rejected(self):
        sample = valid()
        sample["operator_review"]["new_approval_id"] = "reuse-prior"
        with self.assertRaisesRegex(ValueError, "cannot carry approval"):
            M.inspect(sample, now=NOW)

    def test_review_claim_cannot_be_signed_in_unsigned_data(self):
        sample = valid()
        sample["operator_review"]["independent_host_signature"] = True
        with self.assertRaisesRegex(ValueError, "cannot claim"):
            M.inspect(sample, now=NOW)

    def test_generation_replay_refused(self):
        sample = valid()
        sample["proposed_generation"] = 643
        with self.assertRaisesRegex(ValueError, "advance exactly one"):
            M.inspect(sample, now=NOW)

    def test_unknown_private_field_refused(self):
        sample = valid()
        sample["router_admin_token"] = "secret"
        with self.assertRaisesRegex(ValueError, "unexpected"):
            M.inspect(sample, now=NOW)

    def test_telemetry_from_wrong_model_blocked(self):
        sample = valid()
        sample["physical"]["model_sha256"] = "0" * 64
        self.assertIn("PHYSICAL_MODEL_IDENTITY_UNPROVEN",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_unloaded_model_cannot_be_reapproved(self):
        sample = valid()
        sample["physical"]["loaded_model_count"] = 0
        self.assertIn("PHYSICAL_MODEL_IDENTITY_UNPROVEN",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_duplicate_physical_matching_processes_not_accepted(self):
        sample = valid()
        sample["physical"]["matching_process_count"] = 2
        self.assertIn("PHYSICAL_MODEL_IDENTITY_UNPROVEN",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_different_context_not_authorized_from_manifest(self):
        sample = valid()
        sample["physical"]["context_tokens"] = 0
        self.assertIn("PHYSICAL_MODEL_IDENTITY_UNPROVEN",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_overdeclared_slots_fail(self):
        sample = valid()
        sample["capacity"]["requested_slots"] = 5
        self.assertIn("CAPACITY_EXCEEDS_OBSERVED_CONFIGURATION",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_zero_slots_fail(self):
        sample = valid()
        sample["capacity"]["requested_slots"] = 0
        self.assertIn("CAPACITY_EXCEEDS_OBSERVED_CONFIGURATION",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_bad_lan_response_fails(self):
        sample = valid()
        sample["paths"][0]["http"] = 503
        self.assertIn("LAN_DIRECT_COMPLETION_NOT_PROVEN",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_nonexact_model_response_fails(self):
        sample = valid()
        sample["paths"][1]["exact_marker"] = False
        self.assertIn("TAILSCALE_DIRECT_COMPLETION_NOT_PROVEN",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_faked_chat_canary_size_not_admitted(self):
        sample = valid()
        sample["paths"][0]["max_tokens"] = 120
        self.assertIn("LAN_CANARY_BUDGET_TOO_LARGE",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_both_paths_required(self):
        sample = valid()
        sample["paths"][1]["transport"] = "lan"
        with self.assertRaisesRegex(ValueError, "distinct"):
            M.inspect(sample, now=NOW)

    def test_unapproved_previous_generation_is_not_taken_as_authority(self):
        sample = valid()
        sample["previous_approval_expired"] = False
        self.assertIn("PREVIOUS_APPROVAL_STATE_NOT_EXPIRED",
                      M.inspect(sample, now=NOW)["blockers"])

    def test_int_bool_type_confusion_rejected(self):
        sample = valid()
        sample["physical"]["loaded_model_count"] = True
        with self.assertRaisesRegex(ValueError, "invalid loaded"):
            M.inspect(sample, now=NOW)

    def test_invalid_utc_rejected(self):
        sample = valid()
        sample["physical"]["observed_at_utc"] = "2026-10-11 17:55"
        with self.assertRaisesRegex(ValueError, "invalid physical"):
            M.inspect(sample, now=NOW)


if __name__ == "__main__":
    unittest.main()
