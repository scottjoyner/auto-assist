"""Ephemeral-key tests for deny-by-default canonical projection apply protection."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_projection_signed_apply_gate as G  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "official_runtime_projection_approval", ROOT / "scripts" / "approve-runtime-projection.py",
)
A = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = A
SPEC.loader.exec_module(A)
NOW = 1800000000000


def manifest() -> dict:
    return {
        "schema_version": 1, "generation": 7, "expected_current_generation": 6,
        "ttl_seconds": 120, "approved_by": "reviewer_test",
        "approval_id": "approval_distinct_7", "revision": "test_rev_7",
        "require_lan_and_tailscale": True,
        "runtimes": [{
            "runtime_instance_id": "runtime_a", "node_id": "host_a",
            "runtime_kind": "llama_cpp", "runtime_version": "1.0",
            "process_id": "123", "capacity": {
                "parallel_slots": 1, "queue_limit": 0,
                "queue_timeout_seconds": 0, "evidence_ref": "source_ref_test",
            },
            "access_paths": [
                {"base_url": "http://192.168.20.5:1234/v1", "transport": "lan",
                 "preference": 0, "evidence_ref": "source_ref_test"},
                {"base_url": "http://100.101.3.4:1234/v1", "transport": "tailscale",
                 "preference": 1, "evidence_ref": "source_ref_test"},
            ],
            "models": [{
                "model_instance_id": "model_instance_a", "model_key": "model_a",
                "provider_model": "model_a", "artifact_fingerprint": "abcdef0123456789",
                "quantization": "Q8_0", "context_length": 8192,
                "capabilities": ["local_only"], "evidence_ref": "source_ref_test",
            }],
        }],
    }


def public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def sign(key: Ed25519PrivateKey, payload: dict) -> dict:
    return {"payload": payload, "signature": key.sign(G.canonical(payload)).hex()}


def package(m: dict, source_key: Ed25519PrivateKey,
            operator_key: Ed25519PrivateKey) -> dict:
    runtime = m["runtimes"][0]
    source_payload = {
        "audience": G.AUDIENCE, "role": "source-observer",
        "manifest_sha256": hashlib.sha256(G.canonical(m)).hexdigest(),
        "generation": m["generation"], "approval_id": m["approval_id"],
        "issued_at_ms": NOW - 10_000, "expires_at_ms": NOW + 120_000,
        "observations": [{
            "runtime_instance_id": runtime["runtime_instance_id"],
            "node_id": runtime["node_id"], "process_id": runtime["process_id"],
            "physical_observed_at_ms": NOW - 31_000,
            "model_observed_at_ms": NOW - 31_000,
            "capacity_observed_at_ms": NOW - 31_000,
            "loaded_model_fingerprints": [
                a["artifact_fingerprint"] for a in runtime["models"]
            ],
            "max_safe_slots": 2,
            "paths": [{
                "transport": p["transport"],
                "url_sha256": hashlib.sha256(
                    p["base_url"].rstrip("/").encode()
                ).hexdigest(),
                "canary_observed_at_ms": NOW - 15_000,
                "completed": True, "http_status": 200,
            } for p in runtime["access_paths"]],
        }],
    }
    source = sign(source_key, source_payload)
    operator_payload = {
        "audience": G.AUDIENCE, "role": "operator-reviewer",
        "manifest_sha256": source_payload["manifest_sha256"],
        "generation": m["generation"], "approval_id": m["approval_id"],
        "approved_by": m["approved_by"],
        "source_receipt_sha256": hashlib.sha256(G.canonical(source)).hexdigest(),
        "approved_at_ms": NOW - 5_000, "expires_at_ms": NOW + 60_000,
    }
    return {"schema": "runtime-projection-two-signer-v1",
            "source": source, "operator": sign(operator_key, operator_payload)}


class DummyResult:
    def __init__(self, value=None):
        self.value = value

    def single(self):
        return self.value

    def consume(self):
        return None


class DummyTransaction:
    def __init__(self, generation=6, approval_uses=0):
        self.calls = []
        self.generation = generation
        self.approval_uses = approval_uses

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "RETURN coalesce(s.generation, 0) AS generation" in cypher:
            return DummyResult({"generation": self.generation})
        if "RETURN count(a) AS uses" in cypher:
            return DummyResult({"uses": self.approval_uses})
        return DummyResult()


class TestSignedApply(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest()
        self.source_priv = Ed25519PrivateKey.generate()
        self.operator_priv = Ed25519PrivateKey.generate()
        self.evidence = package(self.manifest, self.source_priv, self.operator_priv)

    def signed(self):
        return G.validate(
            self.manifest, self.evidence, public(self.source_priv),
            public(self.operator_priv), NOW,
        )

    def resign_source(self):
        self.evidence["source"] = sign(
            self.source_priv, self.evidence["source"]["payload"]
        )
        self.evidence["operator"]["payload"]["source_receipt_sha256"] = hashlib.sha256(
            G.canonical(self.evidence["source"])
        ).hexdigest()
        self.resign_operator()

    def resign_operator(self):
        self.evidence["operator"] = sign(
            self.operator_priv, self.evidence["operator"]["payload"]
        )

    def test_real_ephemeral_ed25519_pair_authorizes_only_verification(self):
        self.assertEqual(self.signed().generation, 7)
        self.assertEqual(self.signed().approval_id, "approval_distinct_7")

    def test_missing_signed_source_is_rejected(self):
        self.evidence["source"]["signature"] = "0" * 128
        with self.assertRaisesRegex(ValueError, "signature"):
            self.signed()

    def test_missing_signed_operator_is_rejected(self):
        self.evidence["operator"]["signature"] = "0" * 128
        with self.assertRaisesRegex(ValueError, "signature"):
            self.signed()

    def test_source_and_operator_key_cannot_be_the_same(self):
        with self.assertRaisesRegex(ValueError, "independent"):
            G.validate(self.manifest, self.evidence, public(self.source_priv),
                       public(self.source_priv), NOW)

    def test_signed_stale_source_is_rejected(self):
        self.evidence["source"]["payload"]["issued_at_ms"] = NOW - 301_000
        self.evidence["source"]["payload"]["expires_at_ms"] = NOW + 1
        self.resign_source()
        with self.assertRaises(ValueError):
            self.signed()

    def test_signed_stale_canary_is_rejected(self):
        paths = self.evidence["source"]["payload"]["observations"][0]["paths"]
        paths[1]["canary_observed_at_ms"] = NOW - 181_000
        self.resign_source()
        with self.assertRaisesRegex(ValueError, "canary"):
            self.signed()

    def test_signed_future_source_is_rejected(self):
        self.evidence["source"]["payload"]["issued_at_ms"] = NOW + 10_000
        self.evidence["source"]["payload"]["expires_at_ms"] = NOW + 100_000
        self.resign_source()
        with self.assertRaisesRegex(ValueError, "future"):
            self.signed()

    def test_signed_operator_approval_expiry_denied(self):
        self.evidence["operator"]["payload"]["expires_at_ms"] = NOW
        self.resign_operator()
        with self.assertRaisesRegex(ValueError, "expired"):
            self.signed()

    def test_source_runtime_identity_binding(self):
        self.evidence["source"]["payload"]["observations"][0]["process_id"] = "567"
        self.resign_source()
        with self.assertRaisesRegex(ValueError, "physical process"):
            self.signed()

    def test_model_swap_denied_despite_valid_source_signature(self):
        self.evidence["source"]["payload"]["observations"][0][
            "loaded_model_fingerprints"
        ] = ["different"]
        self.resign_source()
        with self.assertRaisesRegex(ValueError, "loaded physical model"):
            self.signed()

    def test_wrong_model_url_denied_even_with_valid_signature(self):
        self.evidence["source"]["payload"]["observations"][0]["paths"][0][
            "url_sha256"
        ] = "0" * 64
        self.resign_source()
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            self.signed()

    def test_wrong_owner_review_approval_denied(self):
        self.evidence["operator"]["payload"]["approved_by"] = "other"
        self.resign_operator()
        with self.assertRaisesRegex(ValueError, "reviewer identity"):
            self.signed()

    def test_cross_generation_replay_denied(self):
        self.manifest["generation"] = 8
        self.manifest["expected_current_generation"] = 7
        with self.assertRaisesRegex(ValueError, "manifest binding"):
            self.signed()

    def test_stolen_operator_receipt_cannot_sign_other_source(self):
        self.evidence["source"]["payload"]["issued_at_ms"] = NOW - 40_000
        self.evidence["source"] = sign(
            self.source_priv, self.evidence["source"]["payload"]
        )
        with self.assertRaisesRegex(ValueError, "exact source signature"):
            self.signed()

    def test_manifest_full_body_change_is_denied(self):
        self.manifest["runtimes"][0]["capacity"]["parallel_slots"] = 2
        with self.assertRaisesRegex(ValueError, "manifest binding"):
            self.signed()

    def test_capacity_cannot_exceed_signed_source_capacity(self):
        self.evidence["source"]["payload"]["observations"][0]["max_safe_slots"] = 0
        self.resign_source()
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.signed()

    def test_reviewer_approval_predates_source_invalid(self):
        self.evidence["operator"]["payload"]["approved_at_ms"] = NOW - 60_000
        self.evidence["operator"]["payload"]["expires_at_ms"] = NOW + 20_000
        self.resign_operator()
        with self.assertRaisesRegex(ValueError, "predates"):
            self.signed()

    def test_untrusted_source_fields_denied(self):
        self.evidence["source"]["payload"]["is_loaded_proof"] = True
        self.resign_source()
        with self.assertRaisesRegex(ValueError, "keys invalid"):
            self.signed()

    def test_dry_run_unchanged_and_structural_manifest_accepted(self):
        self.assertEqual(A.validate_manifest(self.manifest).payload["generation"], 7)

    def test_unsigned_apply_fails_before_connecting_to_neo4j(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            path = Path(tmp) / "manifest.yaml"
            import yaml
            path.write_text(yaml.safe_dump(self.manifest))
            with patch.object(sys, "argv", ["approve-runtime-projection.py", str(path),
                                            "--apply"]), \
                    patch.object(A.GraphDatabase, "driver",
                                 side_effect=AssertionError("DB should never open")):
                self.assertEqual(A.main(), 2)

    def test_signed_attestation_rechecked_inside_transaction_before_any_write(self):
        tx = DummyTransaction()
        corrupted = copy.deepcopy(self.evidence)
        corrupted["operator"]["signature"] = "a" * 128
        with patch.object(A.time, "time", return_value=NOW / 1000):
            with self.assertRaises(ValueError):
                A._apply_transaction(
                    tx, A.validate_manifest(self.manifest), corrupted,
                    public(self.source_priv), public(self.operator_priv),
                )
        self.assertEqual(tx.calls, [])

    def test_transaction_will_not_reuse_existing_approval_receipt(self):
        tx = DummyTransaction(approval_uses=1)
        with patch.object(A.time, "time", return_value=NOW / 1000):
            with self.assertRaisesRegex(RuntimeError, "already used"):
                A._apply_transaction(
                    tx, A.validate_manifest(self.manifest), self.evidence,
                    public(self.source_priv), public(self.operator_priv),
                )
        self.assertFalse(any("SET r.admitted=false" in statement for statement, _ in tx.calls))

    def test_transaction_will_refuse_stale_generation_before_ledger_modifications(self):
        tx = DummyTransaction(generation=5)
        with patch.object(A.time, "time", return_value=NOW / 1000):
            with self.assertRaisesRegex(RuntimeError, "generation compare-and-swap"):
                A._apply_transaction(
                    tx, A.validate_manifest(self.manifest), self.evidence,
                    public(self.source_priv), public(self.operator_priv),
                )
        self.assertFalse(any("SET r.admitted=false" in statement for statement, _ in tx.calls))

    def test_mock_transaction_audits_source_and_operator_without_production_db(self):
        tx = DummyTransaction()
        with patch.object(A.time, "time", return_value=NOW / 1000):
            out = A._apply_transaction(
                tx, A.validate_manifest(self.manifest), self.evidence,
                public(self.source_priv), public(self.operator_priv),
            )
        self.assertEqual(out["generation"], 7)
        self.assertEqual(out["source_receipt_sha256"], self.signed().source_sha256)
        self.assertTrue(any(
            "CREATE (a:RuntimeProjectionApprovalReceipt" in q
            for q, _ in tx.calls
        ))

    def test_public_key_file_requires_restricted_mode(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            path = Path(tmp) / "source.pub"
            path.write_text(public(self.source_priv).hex() + "\n")
            path.chmod(0o666)
            with patch.dict(os.environ, {"ASSISTX_RUNTIME_SOURCE_PUBKEY_FILE": str(path)}):
                with self.assertRaisesRegex(ValueError, "writable"):
                    A._read_trusted_key("ASSISTX_RUNTIME_SOURCE_PUBKEY_FILE")

    def test_unsigned_wrapper_cannot_claim_real_source_role(self):
        self.evidence["schema"] = "fictional-only"
        with self.assertRaisesRegex(ValueError, "schema"):
            self.signed()


if __name__ == "__main__":
    unittest.main()
