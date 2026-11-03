"""Synthetic review-claim signatures; never represent independent custody."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts"
          / "verify-k2-custody-handoff-offline.py")
spec = importlib.util.spec_from_file_location("k2_custody_handoff", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = 1_800_000_000_000
NONCE = "a1" * 32
EVIDENCE = {k: hashlib.sha256(k.encode()).hexdigest() for k in module.EVIDENCE_FIELDS}


def pubkey(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )


def sign(doc: dict, keys: dict[str, Ed25519PrivateKey]) -> bytes:
    unsigned = {k: v for k, v in doc.items() if k != "attestations"}
    doc["attestations"] = {
        role: {
            "public_key_b64": base64.b64encode(pubkey(key)).decode(),
            "signature_b64": base64.b64encode(
                key.sign(
                    module.DOMAIN + role.encode() + b"\0" + module.canonical(unsigned)
                )
            ).decode(),
        }
        for role, key in keys.items()
    }
    return module.canonical(doc)


@pytest.fixture
def signed_fixture(monkeypatch):
    keys = {
        "operator": Ed25519PrivateKey.generate(),
        "custodian": Ed25519PrivateKey.generate(),
    }
    monkeypatch.setattr(
        module, "APPROVED_OPERATOR_PUBLIC_KEY_SHA256",
        hashlib.sha256(pubkey(keys["operator"])).hexdigest(),
    )
    monkeypatch.setattr(
        module, "APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256",
        hashlib.sha256(pubkey(keys["custodian"])).hexdigest(),
    )
    doc = {
        "schema": module.SCHEMA, "node": module.NODE,
        "model_id": module.MODEL_ID, "model_sha256": module.MODEL_SHA256,
        "challenge_hex": NONCE, "issued_at_ms": NOW - 1000,
        "expires_at_ms": NOW + 60_000,
        "custody_evidence": dict(EVIDENCE),
    }
    return keys, doc, sign(doc, keys)


def check(raw: bytes) -> dict:
    return module.verify(raw, expected_challenge_hex=NONCE, now_ms=NOW)


def test_production_key_roots_intentionally_empty():
    code = SCRIPT.read_text()
    assert 'APPROVED_OPERATOR_PUBLIC_KEY_SHA256 = ""' in code
    assert 'APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256 = ""' in code
    assert "subprocess" not in code
    assert "can_dispatch" in code


def test_correct_synthetic_signatures_authenticate_claims_but_never_custody(signed_fixture):
    _, _, raw = signed_fixture
    result = check(raw)
    assert result["operator_signature_valid"] is True
    assert result["custodian_signature_valid"] is True
    assert result["evidence_hash_claims_present"] is True
    for field in (
        "evidence_contents_independently_verified",
        "independent_administration_verified",
        "protected_key_custody_verified",
        "external_antirollback_verified",
        "external_challenge_consumed_once",
        "hardware_attestation_verified",
        "canonical_operator_approval_verified",
        "can_dispatch",
    ):
        assert result[field] is False
    assert result["decision"] == "HOLD"


def test_missing_production_pin_holds_even_if_signed(monkeypatch, signed_fixture):
    _, _, raw = signed_fixture
    monkeypatch.setattr(module, "APPROVED_OPERATOR_PUBLIC_KEY_SHA256", "")
    with pytest.raises(module.CustodyHold, match="external_operator_and_custodian_pins_not_provisioned"):
        check(raw)


def test_same_role_pin_refused(signed_fixture, monkeypatch):
    _, _, raw = signed_fixture
    monkeypatch.setattr(
        module, "APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256",
        module.APPROVED_OPERATOR_PUBLIC_KEY_SHA256,
    )
    with pytest.raises(module.CustodyHold, match="operator_custodian_key_roles_not_separate"):
        check(raw)


def test_untrusted_signer_rejected(signed_fixture, monkeypatch):
    keys, doc, _ = signed_fixture
    other = {"operator": Ed25519PrivateKey.generate(), "custodian": keys["custodian"]}
    with pytest.raises(module.CustodyHold, match="attestation_key_not_preapproved"):
        check(sign(doc, other))


def test_challenge_echo_cannot_be_chosen_from_envelope(signed_fixture):
    _, _, raw = signed_fixture
    with pytest.raises(module.CustodyHold, match="custody_review_challenge_mismatch"):
        module.verify(raw, expected_challenge_hex="f" * 64, now_ms=NOW)


def test_invalid_challenge_shape_rejected(signed_fixture):
    _, _, raw = signed_fixture
    with pytest.raises(module.CustodyHold, match="missing_external_review_challenge"):
        module.verify(raw, expected_challenge_hex="not-hex", now_ms=NOW)


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("node", "xwing", "wrong_model_or_claim_domain"),
    ("model_id", "wrong-model", "wrong_model_or_claim_domain"),
    ("model_sha256", "f" * 64, "wrong_model_or_claim_domain"),
    ("issued_at_ms", True, "custody_claim_window_invalid"),
    ("issued_at_ms", NOW + 1, "custody_claim_window_invalid"),
    ("issued_at_ms", NOW - 90_000_000, "custody_claim_window_invalid"),
    ("expires_at_ms", NOW, "custody_claim_window_invalid"),
    ("expires_at_ms", NOW + 100_000_000, "custody_claim_window_invalid"),
])
def test_signed_invalid_scope_or_window_denied(signed_fixture, field, value, reason):
    keys, doc, _ = signed_fixture
    doc[field] = value
    with pytest.raises(module.CustodyHold, match=reason):
        check(sign(doc, keys))


def test_tampered_claim_signature_rejected(signed_fixture):
    _, _, raw = signed_fixture
    doc = json.loads(raw)
    doc["custody_evidence"]["protected_key_provenance_sha256"] = "f" * 64
    with pytest.raises(module.CustodyHold, match="custody_attestation_signature_invalid"):
        check(module.canonical(doc))


def test_unsigned_reference_claim_rejected(signed_fixture):
    keys, doc, _ = signed_fixture
    doc["custody_evidence"]["external_highwater_anchor_reference_sha256"] = "0" * 64
    with pytest.raises(module.CustodyHold, match="custody_evidence_reference_invalid"):
        check(sign(doc, keys))


def test_missing_evidence_item_rejected(signed_fixture):
    keys, doc, _ = signed_fixture
    doc["custody_evidence"].pop("hardware_attestation_policy_reference_sha256")
    with pytest.raises(module.CustodyHold, match="custody_evidence_schema_invalid"):
        check(sign(doc, keys))


def test_claiming_custody_proven_in_payload_denied(signed_fixture):
    keys, doc, _ = signed_fixture
    doc["custody_verified"] = True
    with pytest.raises(module.CustodyHold, match="unreviewed_claims_fields"):
        check(sign(doc, keys))


def test_challenge_spent_claim_field_denied(signed_fixture):
    keys, doc, _ = signed_fixture
    doc["can_dispatch"] = True
    with pytest.raises(module.CustodyHold, match="unreviewed_claims_fields"):
        check(sign(doc, keys))


def test_duplicate_json_key_rejected(signed_fixture):
    _, _, raw = signed_fixture
    injected = raw.replace(
        b'"node":"x1-370"', b'"node":"x1-370","node":"x1-370"',
    )
    with pytest.raises(module.CustodyHold, match="duplicate_json_key"):
        check(injected)


def test_noncanonical_json_rejected(signed_fixture):
    _, _, raw = signed_fixture
    with pytest.raises(module.CustodyHold, match="noncanonical_claims_bytes"):
        check(json.dumps(json.loads(raw), indent=2).encode())


def test_oversized_input_rejected():
    # Pin validation runs first. Validate input size through isolated pins.
    if not module.SHA.fullmatch("f" * 64):
        pytest.fail("digest regex invalid")
    keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    original = (
        module.APPROVED_OPERATOR_PUBLIC_KEY_SHA256,
        module.APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256,
    )
    module.APPROVED_OPERATOR_PUBLIC_KEY_SHA256 = hashlib.sha256(pubkey(keys[0])).hexdigest()
    module.APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256 = hashlib.sha256(pubkey(keys[1])).hexdigest()
    try:
        with pytest.raises(module.CustodyHold, match="claims_document_size_invalid"):
            check(b"A" * 8193)
    finally:
        (module.APPROVED_OPERATOR_PUBLIC_KEY_SHA256,
         module.APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256) = original


def test_missing_attestation_role_denied(signed_fixture):
    _, _, raw = signed_fixture
    d = json.loads(raw)
    d["attestations"].pop("custodian")
    with pytest.raises(module.CustodyHold, match="missing_or_extra_attestation_role"):
        check(module.canonical(d))


def test_bad_attestation_encoding_denied(signed_fixture):
    _, _, raw = signed_fixture
    d = json.loads(raw)
    d["attestations"]["operator"]["signature_b64"] = "?bad"
    with pytest.raises(module.CustodyHold, match="invalid_signature_encoding"):
        check(module.canonical(d))


def test_cross_role_signature_not_accepted(signed_fixture):
    _, _, raw = signed_fixture
    d = json.loads(raw)
    d["attestations"]["operator"]["signature_b64"] = d["attestations"]["custodian"]["signature_b64"]
    with pytest.raises(module.CustodyHold, match="custody_attestation_signature_invalid"):
        check(module.canonical(d))


def test_cli_never_returns_zero_or_production_authority():
    process = subprocess.run(
        ["python3", "-B", str(SCRIPT), "--expected-challenge", NONCE],
        input=b"{}", capture_output=True, timeout=7, check=False,
    )
    assert process.returncode == 2
    outcome = json.loads(process.stdout)
    assert outcome["decision"] == "HOLD"
    assert outcome["can_dispatch"] is False
    assert outcome["reason_code"] == "external_operator_and_custodian_pins_not_provisioned"
