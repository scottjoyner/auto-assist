"""Synthetic-only Mercury provider-failure denial acceptance checks."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mercury_failure_recovery_mock.py"
spec = importlib.util.spec_from_file_location("mercury_failure_recovery_mock", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def project(**kw):
    return mod.project_failure(upstream_group="provider-account-A", observed_ms=1000, **kw)


class DenyOnlyRecoveryContract(unittest.TestCase):
    def inert(self, result):
        self.assertEqual(result["decision"], "DENY")
        self.assertFalse(result["dispatch_authorized"])
        self.assertFalse(result["automatic_retry_authorized"])
        self.assertFalse(result["automatic_fallback_authorized"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["worker_launches"], 0)
        self.assertIsNone(result["verified_cost_usd"])
        self.assertEqual(result["quota_authority"], "UNVERIFIED")
        self.assertEqual(result["trace_custody"], "UNATTESTED")
        self.assertEqual(len(result["receipt_sha256"]), 64)

    def test_401_402_403_hold_without_retry(self):
        for status in (401, 402, 403):
            with self.subTest(status=status):
                r = project(status_code=status)
                self.inert(r)
                self.assertIn("credentials_or_billing_hold", r["reasons"])
                self.assertTrue(r["requires_owner_review"])

    def test_429_floor_and_higher_retry_after(self):
        a = project(status_code=429)
        b = project(status_code=429, retry_after_ms=30 * 60 * 1000)
        self.inert(a); self.inert(b)
        self.assertEqual(a["advisory_cooldown_until_ms"], 901000)
        self.assertEqual(b["advisory_cooldown_until_ms"], 1801000)
        self.assertIn("upstream_rate_limited", a["reasons"])

    def test_503_floor(self):
        r = project(status_code=503)
        self.inert(r)
        self.assertEqual(r["advisory_cooldown_until_ms"], 301000)

    def test_expired_prior_cooldown_never_grants_request(self):
        r = project(status_code=200, previous_cooldown_until_ms=900)
        self.inert(r)
        self.assertEqual(r["advisory_cooldown_until_ms"], 900)

    def test_prior_cooldown_cannot_shorten_on_429(self):
        r = project(status_code=429, previous_cooldown_until_ms=999999999)
        self.inert(r)
        self.assertEqual(r["advisory_cooldown_until_ms"], 999999999)

    def test_huge_retry_after_quarantines_instead_of_clamping(self):
        r = project(status_code=429, retry_after_ms=100000000)
        self.inert(r)
        self.assertTrue(r["requires_owner_review"])
        self.assertIn("retry_after_untrusted_requires_review", r["reasons"])

    def test_bad_retry_after_type_fails_closed(self):
        for retry in ("120", True, -1, float("inf")):
            r = project(status_code=503, retry_after_ms=retry)
            self.inert(r)
            self.assertTrue(r["requires_owner_review"])

    def test_timeouts_and_lost_ack_quarantine(self):
        for kind in ("timeout", "disconnect", "network_error", "unknown_ack"):
            with self.subTest(kind=kind):
                r = project(failure_kind=kind)
                self.inert(r)
                self.assertIn("unknown_spend_outcome_quarantine", r["reasons"])

    def test_healthy_status_does_not_authorize_without_receipt(self):
        r = project(status_code=200)
        self.inert(r)
        self.assertIn("unverified_provider_outcome", r["reasons"])

    def test_same_upstream_alias_no_fallback(self):
        r = project(status_code=429, fallback_group="provider-account-A")
        self.inert(r)
        self.assertIn("same_upstream_group_not_independent_quota", r["reasons"])

    def test_distinct_group_still_no_automatic_fallback(self):
        r = project(status_code=503, fallback_group="provider-account-B")
        self.inert(r)
        self.assertIn("cross_group_fallback_unverified", r["reasons"])

    def test_invalid_group_is_redacted(self):
        r = mod.project_failure(upstream_group="SECRET BEARER x y",
                                fallback_group="api key leak xyz", status_code=403,
                                observed_ms=1000)
        self.inert(r)
        self.assertIsNone(r["upstream_group"])
        self.assertIsNone(r["fallback_group"])
        self.assertNotIn("SECRET", json.dumps(r))
        self.assertNotIn("api key", json.dumps(r))

    def test_invalid_status_bool_and_missing_denies(self):
        for code in (None, True, "429"):
            r = project(status_code=code)
            self.inert(r)
            self.assertIn("missing_or_invalid_provider_status", r["reasons"])

    def test_clock_rollback_requires_review(self):
        r = project(status_code=429, previous_observed_ms=2000)
        self.inert(r)
        self.assertIn("clock_rollback", r["reasons"])
        self.assertTrue(r["requires_owner_review"])

    def test_overflow_is_quarantined(self):
        r = mod.project_failure(upstream_group="A", observed_ms=2**53 - 2,
                                status_code=503)
        self.inert(r)
        self.assertTrue(r["requires_owner_review"])
        self.assertIn("cooldown_overflow", r["reasons"])

    def test_invalid_prior_metadata_does_not_authorize(self):
        r = project(status_code=429, previous_cooldown_until_ms="unverified")
        self.inert(r)
        self.assertIn("invalid_prior_cooldown", r["reasons"])

    def test_hash_is_stable_without_raw_response(self):
        a = project(status_code=429)
        b = project(status_code=429)
        c = project(status_code=503)
        self.assertEqual(a["receipt_sha256"], b["receipt_sha256"])
        self.assertNotEqual(a["receipt_sha256"], c["receipt_sha256"])


if __name__ == "__main__":
    unittest.main()
