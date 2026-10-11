"""Synthetic-only LM Studio gateway->specific worker signed receipt contract.

All signing keys are TEST-ONLY ephemeral in-memory Ed25519; no machine signers,
provider or model are used. No real execution or authenticated owner is proven.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_lmstudio_worker_request_pair",
    ROOT / "scripts" / "verify_lmstudio_worker_request_pair.py",
)
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)
NOW = 1_800_000_000_000
BOOT = "d867483a-d299-49d0-ac3a-7fa4a7d41c1a"
HASH = "a" * 64
RID = "rq_20261011_00000001"


def raw_public(k: Ed25519PrivateKey) -> bytes:
    return k.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def wrap(k: Ed25519PrivateKey, payload: dict) -> dict:
    # Return an immutable snapshot of the signed payload for realistic replay.
    snapshot = copy.deepcopy(payload)
    return {"payload": snapshot, "signature": k.sign(M.canonical(snapshot)).hex()}


def fixture():
    identity = {
        "boot_id": BOOT, "worker_pid": 4783, "process_start_ticks": 2340,
        "model_sha256": HASH, "gateway_pid": 1535,
        "gateway_start_ticks": 1125, "gateway_port": 1234,
        "worker_port": 45435,
    }
    worker = {
        "audience": M.AUDIENCE, "role": "worker",
        "request_id": RID, "owner_digest": "b" * 64,
        "request_sha256": "c" * 64, "nonce_sha256": "d" * 64,
        "model_instance_id": "liquid/lfm2.5-1.2b-instance-test",
        "worker_identity": identity,
        "started_ms": NOW - 30_000, "completed_ms": NOW - 12_000,
        "completion_state": "COMPLETED", "finish_reason": "stop",
        "output_sha256": "e" * 64, "completion_tokens": 12,
    }
    gateway = {
        "audience": M.AUDIENCE, "role": "gateway",
        "request_id": RID, "owner_digest": "b" * 64,
        "request_sha256": "c" * 64, "nonce_sha256": "d" * 64,
        "model_instance_id": "liquid/lfm2.5-1.2b-instance-test",
        "worker_identity": copy.deepcopy(identity),
        "started_ms": NOW - 35_000, "completed_ms": NOW - 11_000,
        "completion_state": "COMPLETED", "http_status": 200,
        "response_id": "resp_20261011_000001",
        "output_sha256": "e" * 64,
        "worker_receipt_sha256": "",
    }
    return worker, gateway


class TestWorkerRequestPair(unittest.TestCase):
    def setUp(self):
        self.gateway_key = Ed25519PrivateKey.generate()
        self.worker_key = Ed25519PrivateKey.generate()
        self.worker, self.gateway = fixture()

    def check(self, **changes):
        params = {
            "gateway_envelope": self.signed_gateway(),
            "worker_envelope": self.signed_worker(),
            "trusted_gateway_public_key": raw_public(self.gateway_key),
            "trusted_worker_public_key": raw_public(self.worker_key),
            "now_ms": NOW,
        }
        params.update(changes)
        return M.correlate(**params)

    def signed_worker(self):
        return wrap(self.worker_key, self.worker)

    def signed_gateway(self):
        self.gateway["worker_receipt_sha256"] = hashlib.sha256(
            M.canonical(self.signed_worker())
        ).hexdigest()
        return wrap(self.gateway_key, self.gateway)

    def test_valid_test_only_signatures_consistent_no_authority(self):
        r = self.check()
        self.assertEqual(r.worker_pid, 4783)
        self.assertEqual(r.worker_start_ticks, 2340)
        out = M.review_only_result(r)
        self.assertEqual(out["status"], "CRYPTOGRAPHIC_PAIR_CONSISTENT_ONLY")
        self.assertEqual(out["request_id"], RID)
        for flag in ("producer_key_custody_established",
                     "independent_worker_execution_attested",
                     "trusted_owner_binding_established",
                     "signed_operator_approval", "can_renew", "can_dispatch"):
            self.assertFalse(out[flag])

    def test_same_key_for_gateway_and_worker_denied(self):
        with self.assertRaisesRegex(ValueError, "independently"):
            self.check(trusted_gateway_public_key=raw_public(self.worker_key))

    def test_corrupt_worker_signature_denied(self):
        self.gateway["worker_receipt_sha256"] = hashlib.sha256(
            M.canonical(self.signed_worker())
        ).hexdigest()
        worker = self.signed_worker()
        worker["signature"] = "0" * 128
        with self.assertRaisesRegex(ValueError, "signature"):
            self.check(worker_envelope=worker)

    def test_corrupt_gateway_signature_denied(self):
        gateway = self.signed_gateway()
        gateway["signature"] = "0" * 128
        with self.assertRaisesRegex(ValueError, "signature"):
            self.check(gateway_envelope=gateway)

    def test_another_untrusted_gateway_public_key_denied(self):
        rogue = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(ValueError, "signature"):
            self.check(trusted_gateway_public_key=raw_public(rogue))

    def test_another_untrusted_worker_public_key_denied(self):
        rogue = Ed25519PrivateKey.generate()
        with self.assertRaisesRegex(ValueError, "signature"):
            self.check(trusted_worker_public_key=raw_public(rogue))

    def test_worker_response_different_from_gateway_denied(self):
        self.worker["output_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "output_sha256"):
            self.check()

    def test_worker_identity_pid_changed_denied(self):
        self.gateway["worker_identity"]["worker_pid"] = 1006
        with self.assertRaisesRegex(ValueError, "worker_identity"):
            self.check()

    def test_restarted_worker_start_ticks_changed_denied(self):
        self.gateway["worker_identity"]["process_start_ticks"] = 2341
        with self.assertRaisesRegex(ValueError, "worker_identity"):
            self.check()

    def test_wrong_boot_identity_denied(self):
        self.worker["worker_identity"]["boot_id"] = (
            "11111111-2222-4333-8444-555555555555"
        )
        with self.assertRaisesRegex(ValueError, "worker_identity"):
            self.check()

    def test_different_loaded_model_instance_denied(self):
        self.gateway["model_instance_id"] = "liquid/another-model-instance"
        with self.assertRaisesRegex(ValueError, "model_instance_id"):
            self.check()

    def test_malformed_model_instance_denied(self):
        self.worker["model_instance_id"] = "invalid whitespace instance"
        with self.assertRaisesRegex(ValueError, "instance ID"):
            self.check()

    def test_model_digest_mismatch_denied(self):
        self.gateway["worker_identity"]["model_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "worker_identity"):
            self.check()

    def test_unrelated_request_id_denied(self):
        self.gateway["request_id"] = "rq_20261011_00000002"
        with self.assertRaisesRegex(ValueError, "request_id"):
            self.check()

    def test_two_authenticated_owners_not_interchangeable(self):
        self.gateway["owner_digest"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "owner_digest"):
            self.check()

    def test_nonce_substitution_denied(self):
        self.worker["nonce_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "nonce_sha256"):
            self.check()

    def test_changed_request_hash_denied(self):
        self.worker["request_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "request_sha256"):
            self.check()

    def test_full_worker_receipt_hash_must_be_countersigned(self):
        gw = self.signed_gateway()
        self.worker["completed_ms"] -= 500
        with self.assertRaisesRegex(ValueError, "countersign"):
            self.check(gateway_envelope=gw)

    def test_known_request_replay_rejected(self):
        with self.assertRaisesRegex(ValueError, "already witnessed"):
            self.check(already_seen_request_ids=frozenset({RID}))

    def test_missing_external_replay_store_does_not_claim_safe_dedupe(self):
        out = M.review_only_result(self.check())
        self.assertNotIn("durable_replay_rejected", out)
        self.assertFalse(out["can_dispatch"])

    def test_worker_timeout_not_success(self):
        self.worker["completion_state"] = "TIMEOUT"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.check()

    def test_gateway_missing_terminal_response_not_success(self):
        self.gateway["completion_state"] = "INCOMPLETE"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.check()

    def test_worker_truncation_denied(self):
        self.worker["finish_reason"] = "length"
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.check()

    def test_gateway_500_denied(self):
        self.gateway["http_status"] = 500
        with self.assertRaisesRegex(ValueError, "HTTP 200"):
            self.check()

    def test_gateway_http_boolean_not_int_denied(self):
        self.gateway["http_status"] = True
        with self.assertRaisesRegex(ValueError, "HTTP 200"):
            self.check()

    def test_gateway_missing_response_id_denied(self):
        self.gateway["response_id"] = ""
        with self.assertRaisesRegex(ValueError, "response ID"):
            self.check()

    def test_worker_completed_too_late_denied(self):
        self.worker["completed_ms"] = NOW + 10_000
        with self.assertRaisesRegex(ValueError, "future-dated"):
            self.check()

    def test_worker_completed_outside_gateway_request_denied(self):
        self.gateway["completed_ms"] = NOW - 20_000
        with self.assertRaisesRegex(ValueError, "chronology"):
            self.check()

    def test_gateway_started_after_worker_denied(self):
        self.gateway["started_ms"] = NOW - 12_000
        with self.assertRaisesRegex(ValueError, "chronology"):
            self.check()

    def test_stale_signed_execution_receipts_denied(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            self.check(now_ms=NOW + M.MAX_AGE_MS + 30_000)

    def test_completion_exceeding_duration_denied(self):
        self.worker["started_ms"] = NOW - 110_000
        with self.assertRaisesRegex(ValueError, "bounded completion"):
            self.check()

    def test_completion_with_zero_tokens_denied(self):
        self.worker["completion_tokens"] = 0
        with self.assertRaisesRegex(ValueError, "tokens"):
            self.check()

    def test_impossible_model_process_identity_denied(self):
        self.worker["worker_identity"]["worker_pid"] = 1535
        self.gateway["worker_identity"]["worker_pid"] = 1535
        with self.assertRaisesRegex(ValueError, "share"):
            self.check()

    def test_unreviewed_extra_raw_prompt_field_denied(self):
        self.gateway["raw_prompt"] = "do not put private prompts in receipts"
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            self.check()

    def test_signature_not_authoritative_as_owner(self):
        out = M.review_only_result(self.check())
        self.assertFalse(out["trusted_owner_binding_established"])
        self.assertFalse(out["independent_worker_execution_attested"])

    def test_worker_same_nonce_different_owner_fails(self):
        self.worker["owner_digest"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "owner_digest"):
            self.check()


if __name__ == "__main__":
    unittest.main()
