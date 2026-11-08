"""Offline signed owner-grant negative tests; NEVER connect to production Neo4j."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_owner_approval_receipt as approval  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "approve_runtime_receipt_test", ROOT / "scripts/approve-runtime-projection.py"
)
assert SPEC and SPEC.loader
issuer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = issuer
SPEC.loader.exec_module(issuer)


def manifest():
    # Same validator contract as the original production approval tests, but
    # without relying on a running database or a mutable disk receipt.
    return issuer.validate_manifest(
        {
            "schema_version": 1,
            "generation": 3,
            "expected_current_generation": 2,
            "revision": "independent-test-3",
            "approved_by": "test-human-owner",
            "approval_id": "test-only-approval-3",
            "ttl_seconds": 60,
            "require_lan_and_tailscale": True,
            "runtimes": [
                {
                    "runtime_instance_id": "test-live-runtime",
                    "node_id": "test-node",
                    "runtime_kind": "llama_cpp",
                    "runtime_version": "1",
                    "process_id": "1234",
                    "capacity": {
                        "parallel_slots": 1,
                        "queue_limit": 1,
                        "queue_timeout_seconds": 2,
                        "evidence_ref": "synthetic-capacity",
                    },
                    "access_paths": [
                        {
                            "base_url": "http://192.168.1.1:1234/v1",
                            "transport": "lan",
                            "preference": 1,
                            "evidence_ref": "synthetic-lan",
                        },
                        {
                            "base_url": "http://100.64.2.3:1234/v1",
                            "transport": "tailscale",
                            "preference": 2,
                            "evidence_ref": "synthetic-tail",
                        },
                    ],
                    "models": [
                        {
                            "model_instance_id": "test-model",
                            "model_key": "test/model",
                            "provider_model": "test-model",
                            "artifact_fingerprint": "sha256:" + "a" * 64,
                            "quantization": "Q4_K_M",
                            "context_length": 128,
                            "capabilities": ["chat", "local_only"],
                            "evidence_ref": "synthetic-model",
                        }
                    ],
                }
            ],
        }
    )


def valid_signed_receipt(item, private, now):
    receipt = {
        "schema_version": approval.SCHEMA,
        "scope": approval.SCOPE,
        "signature_algorithm": "Ed25519",
        "signature_key_id": approval.KEY_ID,
        "approved_by": item.payload["approved_by"],
        "approval_id": item.payload["approval_id"],
        "manifest_checksum": item.checksum,
        "required_roster_sha256": hashlib.sha256(b"{}").hexdigest(),
        "generation": item.payload["generation"],
        "expected_current_generation": item.payload["expected_current_generation"],
        "issued_at_ms": now - 1000,
        "expires_at_ms": now + 120_000,
        "nonce": "a" * 32,
    }
    receipt["signature"] = (
        base64.urlsafe_b64encode(private.sign(approval.signing_message(receipt))).decode().rstrip("=")
    )
    return receipt


def verify_receipt(item, receipt, key, *, now_ms):
    return approval.verify_receipt(
        item,
        receipt,
        key,
        required_roster_sha256=hashlib.sha256(b"{}").hexdigest(),
        now_ms=now_ms,
    )


def test_real_ed25519_signature_binds_exact_manifest_identity_and_time():
    item = manifest()
    private = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    receipt = valid_signed_receipt(item, private, now)
    digest = verify_receipt(item, receipt, private.public_key(), now_ms=now)
    assert len(digest) == 64
    assert receipt["signature"] not in digest


@pytest.mark.parametrize(
    "field,new_value,reason",
    [
        ("approved_by", "somebody-else", "identity_mismatch"),
        ("approval_id", "new-chat-message", "identity_mismatch"),
        ("generation", 4, "generation_mismatch"),
        ("expected_current_generation", 3, "generation_mismatch"),
        ("manifest_checksum", "0" * 64, "manifest_mismatch"),
        ("required_roster_sha256", "0" * 64, "roster_checksum_mismatch"),
        ("scope", "other-purpose", "domain_invalid"),
        ("signature_key_id", "service-token-v1", "domain_invalid"),
        ("schema_version", "other-version", "domain_invalid"),
        ("signature_algorithm", "HMAC", "domain_invalid"),
        ("nonce", "not-hex", "nonce_invalid"),
        ("generation", True, "integer_claim_invalid"),
        ("approved_by", "replace-me", "identity_invalid"),
    ],
)
def test_tampered_or_wrong_claims_are_refused(field, new_value, reason):
    item = manifest()
    private = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    signed = valid_signed_receipt(item, private, now)
    signed[field] = new_value
    with pytest.raises(ValueError, match=reason):
        verify_receipt(item, signed, private.public_key(), now_ms=now)


def test_different_signer_cannot_reuse_original_receipt():
    item = manifest()
    private = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    signed = valid_signed_receipt(item, private, now)
    with pytest.raises(ValueError, match="signature_invalid"):
        verify_receipt(item, signed, Ed25519PrivateKey.generate().public_key(), now_ms=now)


@pytest.mark.parametrize(
    "offset,expire_delta",
    [
        (-900_000, 1000),
        (-500_000, 100_000),
        (50_000, 100_000),
        (-1000, 301_000),
        (-1000, 0),
    ],
)
def test_expired_future_or_excess_lifetime_refused_even_if_signed(offset, expire_delta):
    item = manifest()
    private = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    receipt = valid_signed_receipt(item, private, now)
    receipt["issued_at_ms"] = now + offset
    receipt["expires_at_ms"] = receipt["issued_at_ms"] + expire_delta
    receipt["signature"] = (
        base64.urlsafe_b64encode(private.sign(approval.signing_message(receipt))).decode().rstrip("=")
    )
    with pytest.raises(ValueError, match="receipt_stale"):
        verify_receipt(item, receipt, private.public_key(), now_ms=now)


def test_receipt_rejects_unlisted_or_missing_fields():
    item = manifest()
    private = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    receipt = valid_signed_receipt(item, private, now)
    receipt["extra_task_grant"] = True
    with pytest.raises(ValueError, match="fields_invalid"):
        verify_receipt(item, receipt, private.public_key(), now_ms=now)
    del receipt["extra_task_grant"]
    del receipt["scope"]
    with pytest.raises(ValueError, match="fields_invalid"):
        verify_receipt(item, receipt, private.public_key(), now_ms=now)


def test_receipt_reader_rejects_duplicate_json_properties(tmp_path):
    p = tmp_path / "fake-receipt.json"
    p.write_text('{"approved_by":"first","approved_by":"forged"}')
    with pytest.raises(ValueError, match="duplicate_owner_approval_field"):
        approval.read_receipt(p)


def test_receipt_reader_rejects_symlink_or_oversized(tmp_path):
    p = tmp_path / "source.json"
    p.write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(OSError):
        approval.read_receipt(link)
    p.write_bytes(b"x" * (approval.MAX_RECEIPT + 1))
    with pytest.raises(ValueError, match="size_invalid"):
        approval.read_receipt(p)


def test_service_owned_fake_public_key_not_a_trust_anchor(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root-owned key simulation needs unprivileged test process")
    key = Ed25519PrivateKey.generate()
    fake = tmp_path / "owner.pub"
    fake.write_bytes(
        key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    fake.chmod(0o600)
    monkeypatch.setattr(approval, "TRUST_PATH", fake)
    with pytest.raises(ValueError, match="trust_path_untrusted"):
        approval.load_root_owned_key()


def test_unprovisioned_public_key_cannot_be_found(tmp_path, monkeypatch):
    monkeypatch.setattr(approval, "TRUST_PATH", tmp_path / "missing-key.pub")
    # A temporary user-controlled parent may fail ownership checks before
    # reaching the missing leaf. Both failure modes are security PASS.
    with pytest.raises((FileNotFoundError, ValueError)):
        approval.load_root_owned_key()


def test_unsigned_apply_refused_before_graph_and_dry_run_stays_independent(tmp_path, monkeypatch, capsys):
    item = manifest()
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(item.payload))
    monkeypatch.setattr(issuer, "load_required_node_roster", lambda _: {"synthetic": "roster"})
    monkeypatch.setattr(issuer, "validate_required_node_coverage", lambda *_: None)
    monkeypatch.setattr(
        issuer.GraphDatabase, "driver", lambda *_a, **_k: pytest.fail("graph opened before owner authority")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "approve-runtime-projection.py",
            str(path),
            "--apply",
            "--required-nodes-file",
            str(tmp_path / "fake-roster.json"),
        ],
    )
    assert issuer.main() == 2
    assert "BLOCKED" in capsys.readouterr().err
    assert not (tmp_path / "manifest.approval-evidence.json").exists()


def test_forged_receipt_with_no_root_trust_refused_before_graph(tmp_path, monkeypatch):
    item = manifest()
    key = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(item.payload))
    receipt = tmp_path / "owner-receipt.json"
    receipt.write_text(json.dumps(valid_signed_receipt(item, key, now)))
    monkeypatch.setattr(issuer, "load_required_node_roster", lambda _: {"synthetic": "roster"})
    monkeypatch.setattr(issuer, "validate_required_node_coverage", lambda *_: None)
    monkeypatch.setattr(
        issuer.GraphDatabase, "driver", lambda *_a, **_k: pytest.fail("graph opened on untrusted receipt")
    )
    monkeypatch.setattr(approval, "TRUST_PATH", tmp_path / "missing-root-key.pub")
    monkeypatch.setattr(issuer, "load_root_owned_key", approval.load_root_owned_key)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "approve-runtime-projection.py",
            str(path),
            "--apply",
            "--required-nodes-file",
            str(tmp_path / "fake-roster.json"),
            "--owner-approval-receipt",
            str(receipt),
        ],
    )
    assert issuer.main() == 2


class FakeQueryResult:
    def __init__(self, generation):
        self.generation = generation

    def single(self):
        return {"generation": self.generation}


class FakeTx:
    def __init__(self, generation):
        self.generation = generation
        self.queries = []

    def run(self, query, **_kwargs):
        self.queries.append(query)
        return FakeQueryResult(self.generation)


def test_atomic_generation_lock_before_retiring_any_model(monkeypatch):
    item = manifest()
    key = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    receipt = valid_signed_receipt(item, key, now)
    monkeypatch.setattr(issuer, "validate_required_node_coverage", lambda *_: None)
    # Simulate another writer having advanced the generation to 3 already;
    # atomic increment returns 4 and the write transaction must abort there.
    tx = FakeTx(generation=4)
    with pytest.raises(RuntimeError, match="compare-and-swap failed"):
        issuer._apply_transaction(tx, item, now, {}, receipt, key.public_key())
    assert len(tx.queries) == 1
    assert "SET s.generation=coalesce(s.generation,0)+1" in tx.queries[0]
    assert "RETURN s.generation AS generation" in tx.queries[0]
    assert "SET r.admitted=false" not in tx.queries[0]


def test_invalid_owner_receipt_stops_transaction_before_any_neo4j_query(monkeypatch):
    item = manifest()
    key = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    receipt = valid_signed_receipt(item, key, now)
    receipt["signature"] = "0" * 86
    tx = FakeTx(generation=3)
    monkeypatch.setattr(
        issuer, "validate_required_node_coverage", lambda *_: pytest.fail("profile roster checked before signature")
    )
    with pytest.raises(ValueError):
        issuer._apply_transaction(tx, item, now, {}, receipt, key.public_key())
    assert tx.queries == []


class SuccessfulQuery:
    def __init__(self, generation: int):
        self.generation = generation

    def single(self):
        return {"generation": self.generation}

    def consume(self):
        return None


class SuccessfulFakeTx:
    def __init__(self, generation: int):
        self.generation = generation
        self.calls = []

    def run(self, statement, **params):
        self.calls.append((statement, params))
        return SuccessfulQuery(self.generation)


def test_valid_receipt_persists_exact_digest_on_canonical_graph_state(monkeypatch):
    item = manifest()
    key = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    receipt = valid_signed_receipt(item, key, now)
    expected = verify_receipt(item, receipt, key.public_key(), now_ms=now)
    monkeypatch.setattr(issuer, "validate_required_node_coverage", lambda *_: None)
    tx = SuccessfulFakeTx(generation=3)
    result = issuer._apply_transaction(tx, item, now, {}, receipt, key.public_key())
    assert result["generation"] == 3
    assert result["owner_approval_receipt_sha256"] == expected
    assert tx.calls
    assert "SET s.generation=coalesce(s.generation,0)+1" in tx.calls[0][0]
    final_statements = [(sql, params) for sql, params in tx.calls if "s.owner_approval_receipt_sha256" in sql]
    assert len(final_statements) == 1
    assert final_statements[0][1]["owner_approval_receipt_sha256"] == expected
    assert final_statements[0][1]["manifest_checksum"] == item.checksum


def test_receipt_expiring_during_long_write_aborts_before_final_approval(monkeypatch):
    item = manifest()
    key = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    receipt = valid_signed_receipt(item, key, now)
    monkeypatch.setattr(issuer, "validate_required_node_coverage", lambda *_: None)
    # The initial check uses the supplied transaction time; later the wall
    # clock has passed expiry and must fail before setting final approved.
    monkeypatch.setattr(issuer.time, "time", lambda: (now + 200_000) / 1000)
    tx = SuccessfulFakeTx(generation=3)
    with pytest.raises(ValueError, match="receipt_stale"):
        issuer._apply_transaction(tx, item, now, {}, receipt, key.public_key())
    assert tx.calls
    assert not any("s.owner_approval_receipt_sha256" in q for q, _ in tx.calls)


def test_independent_roster_substitution_invalidates_still_valid_signature():
    item = manifest()
    key = Ed25519PrivateKey.generate()
    now = int(time.time() * 1000)
    receipt = valid_signed_receipt(item, key, now)
    # Same manifest and genuine owner signature, but a different independent
    # roster MUST be rejected before connecting to the approval graph.
    different_roster = hashlib.sha256(b'{"node_ids":["other-node"]}').hexdigest()
    with pytest.raises(ValueError, match="roster_checksum_mismatch"):
        approval.verify_receipt(
            item,
            receipt,
            key.public_key(),
            required_roster_sha256=different_roster,
            now_ms=now,
        )
