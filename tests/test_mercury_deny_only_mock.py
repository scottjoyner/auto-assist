"""No-network negative tests for the deny-only Mercury mock adapter."""
import importlib.util
from pathlib import Path
import unittest

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "mercury_deny_only_mock.py"
spec = importlib.util.spec_from_file_location("mercury_deny_only_mock", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class DenyOnlyContract(unittest.TestCase):
    def test_healthy_synthetic_never_grants_authority(self):
        result = m.evaluate_mock(m.good_synthetic_request())
        self.assertEqual(result["decision"], "DENY")
        self.assertFalse(result["dispatch_authorized"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["production_execution_commands"], 0)

    def test_wrong_node(self):
        inp = m.good_synthetic_request()
        inp["lease_node_id"] = "beelink"
        self.assertIn("wrong_node", m.evaluate_mock(inp)["reasons"])

    def test_expiry_and_clock_rollback_fence(self):
        inp = m.good_synthetic_request()
        inp["lease_expires_ms"] = inp["now_ms"]
        self.assertIn("stale_or_expired_lease", m.evaluate_mock(inp)["reasons"])
        inp["lease_epoch"] = -1
        self.assertIn("invalid_fencing_epoch", m.evaluate_mock(inp)["reasons"])

    def test_replay(self):
        inp = m.good_synthetic_request()
        inp["replayed"] = True
        self.assertIn("replay_or_unverified_nonce", m.evaluate_mock(inp)["reasons"])

    def test_zero_quota(self):
        inp = m.good_synthetic_request()
        inp["quota_used"] = 1
        self.assertIn("quota_exhausted", m.evaluate_mock(inp)["reasons"])

    def test_cooldowns(self):
        for status in (429, 503):
            with self.subTest(status=status):
                inp = m.good_synthetic_request()
                inp["provider_http_status"] = status
                self.assertIn("provider_circuit_open", m.evaluate_mock(inp)["reasons"])

    def test_access_failures(self):
        for status in (401, 402, 403):
            with self.subTest(status=status):
                inp = m.good_synthetic_request()
                inp["provider_http_status"] = status
                self.assertIn("access_denied", m.evaluate_mock(inp)["reasons"])

    def test_missing_and_bad_types(self):
        inp = m.good_synthetic_request()
        inp.pop("lease_epoch")
        self.assertIn("missing_required_field", m.evaluate_mock(inp)["reasons"])
        inp["lease_epoch"] = True
        self.assertIn("invalid_numeric_type", m.evaluate_mock(inp)["reasons"])

    def test_missing_trace_or_archive(self):
        for flag, reason in (("trace_capture_ready", "trace_capture_unavailable"),
                             ("independent_archive_ack", "no_independent_custody")):
            inp = m.good_synthetic_request()
            inp[flag] = False
            self.assertIn(reason, m.evaluate_mock(inp)["reasons"])

    def test_bad_user_identifiers_not_reflected(self):
        inp = m.good_synthetic_request()
        inp["worker_id"] = "SECRET API KEY with spaces and sensitive values"
        result = m.evaluate_mock(inp)
        self.assertIsNone(result["identifiers"]["worker_id"])
        self.assertNotIn("SECRET", str(result))

    def test_hash_stability(self):
        inp = m.good_synthetic_request()
        self.assertEqual(m.evaluate_mock(inp)["receipt_sha256"],
                         m.evaluate_mock(inp)["receipt_sha256"])


if __name__ == "__main__":
    unittest.main()
