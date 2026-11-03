"""Synthetic-only Ed25519 verifier tests; private keys never leave test process."""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SOURCE = Path(__file__).resolve().parents[1] / "scripts/verify-x1-k2-signed-witness-offline.py"
SPEC = importlib.util.spec_from_file_location("signed_witness_verifier", SOURCE)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
NOW = 1_800_000_000_000
CHALLENGE = "a1" * 32


def observation():
    return {
        "kind": module.OBSERVATION_KIND,
        "node": "x1-370",
        "collected_at_utc": datetime.fromtimestamp((NOW - 12_000) / 1000, UTC).isoformat(),
        "gate": "HOLD",
        "reasons": ["independent_signed_witness_and_approval_missing"],
        "app_version": "0.4.26+4",
        "can_dispatch": False,
        "operator_approval_verified": False,
        "cryptographic_witness_signature_present": False,
        "reviewed_app_version_match": True,
        "model_artifact_sha256_match": True,
        "physical_process_socket_chain_verified": True,
        "model_loaded_physically_verified": True,
        "process": {
            "pid": 3525,
            "start_ticks": 1761,
            "boot_id_sha256": "b" * 64,
            "executable_target_verified": True,
            "installed_executable_sha256_match": True,
            "model_file_mapped_in_process_tree": True,
            "per_transport_socket_owner_verified": {
                "loopback": True, "lan": True, "tailscale": True
            },
            "proxies": {
                "lan": {
                    "pid": 8991, "start_ticks": 2572,
                    "running_executable_sha256": "c" * 64,
                    "exact_forward_to_loopback_configured": True,
                    "listener_inode_owned": True,
                },
                "tailscale": {
                    "pid": 8992, "start_ticks": 2572,
                    "running_executable_sha256": "c" * 64,
                    "exact_forward_to_loopback_configured": True,
                    "listener_inode_owned": True,
                },
            },
        },
        "paths": {
            name: {"reachable": True, "loaded_count": 1,
                   "expected_model_present": True}
            for name in ("loopback", "lan", "tailscale")
        },
    }


def fixture(private=None, *, obs=None, challenge=CHALLENGE):
    key = private or Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    entry = {
        "schema": module.SCHEMA,
        "node": module.NODE,
        "expected_challenge_hex": challenge,
        "issued_at_ms": NOW - 3000,
        "expires_at_ms": NOW + 60_000,
        "witness_public_key_b64": base64.b64encode(public).decode(),
        "observation": obs if obs is not None else observation(),
    }
    signed = module.canonical(entry)
    entry["signature_b64"] = base64.b64encode(key.sign(signed)).decode()
    return entry, hashlib.sha256(public).hexdigest()


def validate(monkeypatch, doc=None, *, pin=None, now=NOW, challenge=CHALLENGE):
    if doc is None:
        doc, trusted = fixture()
    else:
        _, trusted = fixture()
    monkeypatch.setattr(module, "TRUSTED_WITNESS_KEY_SHA256", pin or trusted)
    return module.verify(module.canonical(doc), expected_challenge_hex=challenge,
                         now_ms=now)


def signed(monkeypatch, obs=None):
    doc, pin = fixture(obs=obs)
    monkeypatch.setattr(module, "TRUSTED_WITNESS_KEY_SHA256", pin)
    return doc


def test_no_trust_root_fails_closed():
    doc, _ = fixture()
    with pytest.raises(module.WitnessHold, match="no_independent_witness_trust_root"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE,
                      now_ms=NOW)


def test_test_pinned_signature_only_results_in_hold(monkeypatch):
    doc = signed(monkeypatch)
    result = module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE,
                           now_ms=NOW)
    assert result["signature_valid"] is True
    assert result["observation_claims_complete"] is True
    assert result["decision"] == "HOLD"
    assert result["independent_host_custody_verified"] is False
    assert result["operator_approval_verified"] is False
    assert result["can_dispatch"] is False
    assert result["challenge_bound"] is True


def test_wrong_challenge_replay_denied(monkeypatch):
    doc = signed(monkeypatch)
    with pytest.raises(module.WitnessHold, match="challenge_mismatch_or_replay"):
        module.verify(module.canonical(doc), expected_challenge_hex="aa" * 32,
                      now_ms=NOW)


def test_unapproved_key_denied(monkeypatch):
    doc, _ = fixture()
    monkeypatch.setattr(module, "TRUSTED_WITNESS_KEY_SHA256", "d" * 64)
    with pytest.raises(module.WitnessHold, match="witness_signer_not_independently_approved"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_correct_key_invalid_signature_denied(monkeypatch):
    doc = signed(monkeypatch)
    doc["observation"]["process"]["pid"] = 3526
    with pytest.raises(module.WitnessHold, match="invalid_witness_signature"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


@pytest.mark.parametrize("delta", [150_000, -90_000])
def test_observation_time_outside_fresh_window_denied(monkeypatch, delta):
    obs = observation()
    obs["collected_at_utc"] = datetime.fromtimestamp(
        (NOW - delta) / 1000, UTC).isoformat()
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="observation_expired_or_future"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


@pytest.mark.parametrize("issued,expires", [
    (NOW - 130_000, NOW + 50_000),
    (NOW + 1, NOW + 50_000),
    (NOW - 2000, NOW - 1),
    (NOW - 1000, NOW + 400_000),
    (True, NOW + 50_000),
])
def test_bad_witness_time_denied(monkeypatch, issued, expires):
    doc = signed(monkeypatch)
    doc["issued_at_ms"], doc["expires_at_ms"] = issued, expires
    with pytest.raises(module.WitnessHold, match="stale_or_overlong_witness|invalid_witness_time_type"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


@pytest.mark.parametrize("path", ["lan", "tailscale", "loopback"])
def test_missing_one_loaded_path_denied(monkeypatch, path):
    obs = observation()
    obs["paths"][path]["expected_model_present"] = False
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="missing_verified_expected_model_present"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_wrong_proxy_owner_or_command_denied(monkeypatch):
    for key in ("listener_inode_owned", "exact_forward_to_loopback_configured"):
        obs = observation()
        obs["process"]["proxies"]["lan"][key] = False
        doc = signed(monkeypatch, obs)
        with pytest.raises(module.WitnessHold, match="missing_verified_" + key):
            module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_missing_executable_or_unmapped_model_denied(monkeypatch):
    for key in ("executable_target_verified", "model_file_mapped_in_process_tree"):
        obs = observation()
        obs["process"][key] = False
        doc = signed(monkeypatch, obs)
        with pytest.raises(module.WitnessHold, match="missing_verified_" + key):
            module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_malicious_self_admitting_record_denied_even_if_signed(monkeypatch):
    obs = observation()
    obs["can_dispatch"] = True
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="self_admitting_observation_forbidden"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_ambiguous_duplicate_json_rejected(monkeypatch):
    signed(monkeypatch)
    raw = b'{"schema":"a","schema":"b"}'
    with pytest.raises(module.WitnessHold, match="duplicate_json_key"):
        module.verify(raw, expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_nonfinite_json_rejected(monkeypatch):
    signed(monkeypatch)
    raw = b'{"schema":NaN}'
    with pytest.raises(module.WitnessHold, match="nonfinite_json_number"):
        module.verify(raw, expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_oversized_evidence_rejected(monkeypatch):
    signed(monkeypatch)
    with pytest.raises(module.WitnessHold, match="unsigned_document_size_invalid"):
        module.verify(b"{" * (module.MAX_BYTES + 1),
                      expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_bogus_signature_encoding_denied(monkeypatch):
    doc = signed(monkeypatch)
    doc["signature_b64"] = "not base64??"
    with pytest.raises(module.WitnessHold, match="invalid_signature_or_key_encoding"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_actual_unsigned_x1_observation_cannot_verify(monkeypatch):
    obs = observation()
    obs["model_loaded_physically_verified"] = False
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="missing_verified_model_loaded_physically_verified"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)



def test_observation_postdates_witness_issuance_denied(monkeypatch):
    obs = observation()
    obs["collected_at_utc"] = datetime.fromtimestamp(
        (NOW - 1000) / 1000, UTC).isoformat()
    doc = signed(monkeypatch, obs)
    with pytest.raises(
        module.WitnessHold, match="observation_not_bound_to_signed_issuance"
    ):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE,
                      now_ms=NOW)


def test_expired_issued_at_even_with_fresh_obs_denied(monkeypatch):
    doc = signed(monkeypatch)
    doc["issued_at_ms"] = NOW - 150_000
    with pytest.raises(module.WitnessHold, match="stale_or_overlong_witness"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE,
                      now_ms=NOW)


def test_non_boolean_owner_proof_rejected(monkeypatch):
    obs = observation()
    obs["process"]["per_transport_socket_owner_verified"]["lan"] = 1
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="transport_socket_ownership_unverified"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE,
                      now_ms=NOW)



def test_signed_unreviewed_runtime_version_denied(monkeypatch):
    obs = observation()
    obs["app_version"] = "0.4.28+2"
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="unreviewed_runtime_version_claim"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE,
                      now_ms=NOW)


def test_signed_success_flags_but_error_reason_denied(monkeypatch):
    obs = observation()
    obs["reasons"].append("socket_inode_owner_unverified")
    doc = signed(monkeypatch, obs)
    with pytest.raises(
        module.WitnessHold, match="internally_contradictory_physical_observation"
    ):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE,
                      now_ms=NOW)


def test_signed_process_error_label_denied(monkeypatch):
    obs = observation()
    obs["process"]["collection_error_kind"] = "PermissionError"
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="process_evidence_contains_unverified_collection"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE,
                      now_ms=NOW)
