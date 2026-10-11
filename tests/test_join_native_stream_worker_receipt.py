"""Offline join regression: native SSE selection vs signed TEST request pair."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import join_native_stream_worker_receipt as J  # noqa: E402
import test_verify_lmstudio_native_stream_instance as S  # noqa: E402
import test_verify_lmstudio_worker_request_pair as F  # noqa: E402

NOW = F.NOW
RID = F.RID


def proof():
    test_keys = F.TestWorkerRequestPair()
    test_keys.setUp()
    test_keys.worker["output_sha256"] = hashlib.sha256(S.CONTENT.encode()).hexdigest()
    test_keys.gateway["output_sha256"] = test_keys.worker["output_sha256"]
    test_keys.gateway["response_id"] = S.RESPONSE
    worker = test_keys.signed_worker()
    gateway = test_keys.signed_gateway()
    args = {
        "sse_data": "".join(S.fixture()).encode("utf-8"),
        "expected_loaded_instance_id": S.INSTANCE,
        "worker_envelope": worker,
        "gateway_envelope": gateway,
        "trusted_worker_public_key": F.raw_public(test_keys.worker_key),
        "trusted_gateway_public_key": F.raw_public(test_keys.gateway_key),
        "expected_request_id": RID,
        "now_ms": NOW,
    }
    return args, test_keys


class TestNativeWorkerJoin(unittest.TestCase):
    def test_complete_synthetic_pair_only_consistency(self):
        params, _ = proof()
        r = J.join(**params)
        self.assertEqual(r["model_instance_id"], S.INSTANCE)
        self.assertEqual(r["status"], "NATIVE_STREAM_AND_TEST_RECEIPTS_CONSISTENT_ONLY")
        for label in (
            "producer_public_keys_independently_trusted",
            "physical_worker_process_execution_attested",
            "ingress_owner_binding_attested",
            "live_model_or_signer_contacted", "operator_approval_present",
            "can_renew", "can_dispatch",
        ):
            self.assertFalse(r[label], label)

    def test_stream_instance_differs_from_signed_worker(self):
        params, t = proof()
        t.worker["model_instance_id"] = "other-preloaded-instance"
        t.gateway["model_instance_id"] = "other-preloaded-instance"
        params["worker_envelope"] = t.signed_worker()
        params["gateway_envelope"] = t.signed_gateway()
        with self.assertRaisesRegex(ValueError, "model instance"):
            J.join(**params)

    def test_native_response_id_not_gateway_receipt(self):
        params, t = proof()
        t.gateway["response_id"] = "resp_DIFFERENT_12345678"
        params["gateway_envelope"] = t.signed_gateway()
        with self.assertRaisesRegex(ValueError, "response ID"):
            J.join(**params)

    def test_worker_output_digest_differs_from_native_stream(self):
        params, t = proof()
        t.worker["output_sha256"] = "a" * 64
        t.gateway["output_sha256"] = "a" * 64
        params["worker_envelope"] = t.signed_worker()
        params["gateway_envelope"] = t.signed_gateway()
        with self.assertRaisesRegex(ValueError, "output digest"):
            J.join(**params)

    def test_request_id_not_derived_from_native_response_id(self):
        params, _ = proof()
        params["expected_request_id"] = "another_ingress_12345"
        with self.assertRaisesRegex(ValueError, "expected ingress request"):
            J.join(**params)

    def test_invalid_expected_request_id_refused(self):
        params, _ = proof()
        params["expected_request_id"] = "unsafe id"
        with self.assertRaisesRegex(ValueError, "malformed"):
            J.join(**params)

    def test_known_request_replay_refused(self):
        params, _ = proof()
        params["already_seen_request_ids"] = frozenset({RID})
        with self.assertRaisesRegex(ValueError, "already witnessed"):
            J.join(**params)

    def test_forged_worker_signature_rejected(self):
        params, _ = proof()
        params["worker_envelope"]["signature"] = "0" * 128
        with self.assertRaisesRegex(ValueError, "signature"):
            J.join(**params)

    def test_invalid_gateway_owner_binding_denied(self):
        params, t = proof()
        t.gateway["owner_digest"] = "f" * 64
        params["gateway_envelope"] = t.signed_gateway()
        with self.assertRaisesRegex(ValueError, "owner_digest"):
            J.join(**params)

    def test_future_or_stale_receipts_rejected(self):
        params, _ = proof()
        params["now_ms"] = NOW + F.M.MAX_AGE_MS + 40_000
        with self.assertRaisesRegex(ValueError, "stale"):
            J.join(**params)

    def test_caller_supplied_parsed_or_forged_dict_is_rejected(self):
        params, _ = proof()
        params["sse_data"] = {"model_instance_id": S.INSTANCE}
        with self.assertRaisesRegex(ValueError, "size budget"):
            J.join(**params)

    def test_raw_sse_unexpected_model_load_cannot_be_overridden(self):
        params, _ = proof()
        events = S.fixture()
        events.insert(1, S.event("model_load.start", model_instance_id=S.INSTANCE))
        params["sse_data"] = "".join(events).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "unadmitted event"):
            J.join(**params)


    def test_malformed_worker_physical_boot_id_rejected(self):
        params, t = proof()
        t.worker["worker_identity"]["boot_id"] = "fake-boot"
        t.gateway["worker_identity"]["boot_id"] = "fake-boot"
        params["worker_envelope"] = t.signed_worker()
        params["gateway_envelope"] = t.signed_gateway()
        with self.assertRaisesRegex(ValueError, "boot ID"):
            J.join(**params)


if __name__ == "__main__":
    unittest.main()
