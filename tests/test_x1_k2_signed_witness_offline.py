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


@pytest.fixture(autouse=True)
def _synthetic_binary_approval_pins(monkeypatch):
    # Deliberately absent from installed production verifier.
    monkeypatch.setattr(module, "APPROVED_RUNTIME_EXECUTABLE_SHA256", "e" * 64)
    monkeypatch.setattr(module, "APPROVED_PROXY_EXECUTABLE_SHA256", "c" * 64)
    # Synthetic reference boot identity, never trusted from an envelope.
    monkeypatch.setattr(module, "TRUSTED_CURRENT_BOOT_ID_SHA256", "b" * 64)
    # Only a test fixture; real external source remains unconfigured.
    monkeypatch.setattr(module, "TRUSTED_CURRENT_PROCESS_IDENTITY", {
        "node": "x1-370",
        "boot_id_sha256": "b" * 64,
        "observed_at_ms": NOW - 500,
        "runtime": {"pid": 3525, "start_ticks": 1761},
        "proxies": {
            "lan": {"pid": 8991, "start_ticks": 2572},
            "tailscale": {"pid": 8992, "start_ticks": 2572},
        },
    })


def observation():
    return {
        "kind": module.OBSERVATION_KIND,
        "node": "x1-370",
        "collected_at_utc": datetime.fromtimestamp((NOW - 12_000) / 1000, UTC).isoformat(),
        "gate": "HOLD",
        "reasons": ["independent_signed_witness_and_approval_missing"],
        "app_version": "0.4.26+4",
        "model_id_expected": module.EXPECTED_MODEL_ID,
        "model_artifact_sha256_observed": module.EXPECTED_MODEL_SHA256,
        "model_artifact_bytes_observed": module.EXPECTED_MODEL_BYTES,
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
            "running_executable_sha256": "e" * 64,
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
    # Tamper with a semantically valid field so the independent process
    # identity fence cannot mask the cryptographic signature rejection.
    doc["observation"]["paths"]["lan"]["loaded_count"] = 2
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


def test_signed_success_claim_with_collection_stage_alone_denied(monkeypatch):
    obs = observation()
    # A claimed successful physical chain must not smuggle a collection
    # failure marker without also specifying the old error-kind field.
    obs["process"]["collection_error_stage"] = "runtime_executable_link"
    doc = signed(monkeypatch, obs)
    with pytest.raises(
        module.WitnessHold, match="process_evidence_contains_unverified_collection"
    ):
        module.verify(
            module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW
        )


def test_actual_two_tier_observer_fixture_matches_signed_verifier(monkeypatch, tmp_path):
    # This imports the previous stacked PR's real observer code and runs
    # its isolated filesystem/socket fixture. No live /proc or API is touched.
    from test_x1_k2_physical_witness import _observe_fixture

    real_observer_output = _observe_fixture(tmp_path)
    # The upstream fixture runs a tiny synthetic binary/model. Supply only
    # that fixture's measured hashes as TEST-ONLY policy pins, while the
    # production source keeps every executable approval pin empty.
    monkeypatch.setattr(
        module, "EXPECTED_MODEL_BYTES",
        real_observer_output["model_artifact_bytes_observed"],
    )
    monkeypatch.setattr(
        module, "APPROVED_RUNTIME_EXECUTABLE_SHA256",
        real_observer_output["process"]["running_executable_sha256"],
    )
    monkeypatch.setattr(
        module, "APPROVED_PROXY_EXECUTABLE_SHA256",
        real_observer_output["process"]["proxies"]["lan"]["running_executable_sha256"],
    )
    monkeypatch.setattr(
        module, "TRUSTED_CURRENT_BOOT_ID_SHA256",
        real_observer_output["process"]["boot_id_sha256"],
    )
    monkeypatch.setattr(
        module, "TRUSTED_CURRENT_PROCESS_IDENTITY",
        {
            "node": "x1-370",
            "boot_id_sha256": real_observer_output["process"]["boot_id_sha256"],
            "observed_at_ms": NOW - 500,
            "runtime": {
                "pid": real_observer_output["process"]["pid"],
                "start_ticks": real_observer_output["process"]["start_ticks"],
            },
            "proxies": {
                kind: {"pid": proxy["pid"], "start_ticks": proxy["start_ticks"]}
                for kind, proxy in real_observer_output["process"]["proxies"].items()
            },
        },
    )
    assert real_observer_output["physical_process_socket_chain_verified"]
    assert real_observer_output["model_loaded_physically_verified"]
    assert real_observer_output["gate"] == "HOLD"
    real_observer_output["collected_at_utc"] = datetime.fromtimestamp(
        (NOW - 12_000) / 1000, UTC).isoformat()
    doc = signed(monkeypatch, real_observer_output)
    result = module.verify(module.canonical(doc),
                           expected_challenge_hex=CHALLENGE, now_ms=NOW)
    assert result["signature_valid"]
    assert result["observation_claims_complete"]
    assert result["independent_host_custody_verified"] is False
    assert result["can_dispatch"] is False
    assert result["decision"] == "HOLD"


def test_actual_two_tier_observer_denied_when_model_absent(monkeypatch, tmp_path):
    from test_x1_k2_physical_witness import _observe_fixture

    report = _observe_fixture(tmp_path, loaded=False)
    assert report["model_loaded_physically_verified"] is False
    report["collected_at_utc"] = datetime.fromtimestamp(
        (NOW - 12_000) / 1000, UTC).isoformat()
    doc = signed(monkeypatch, report)
    with pytest.raises(module.WitnessHold, match="internally_contradictory_physical_observation"):
        module.verify(module.canonical(doc),
                      expected_challenge_hex=CHALLENGE, now_ms=NOW)



def test_production_binary_approval_pins_are_empty_in_source():
    source = SOURCE.read_text()
    assert 'APPROVED_RUNTIME_EXECUTABLE_SHA256 = ""' in source
    assert 'APPROVED_PROXY_EXECUTABLE_SHA256 = ""' in source


def test_missing_approved_binary_fingerprints_denies_even_valid_signature(monkeypatch):
    doc = signed(monkeypatch)
    monkeypatch.setattr(module, "APPROVED_RUNTIME_EXECUTABLE_SHA256", "")
    with pytest.raises(module.WitnessHold, match="independently_approved_binary_fingerprints_missing"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


@pytest.mark.parametrize("bad_fields", [
    {"model_artifact_sha256_observed": "f" * 64},
    {"model_artifact_bytes_observed": 2_159_424_895},
    {"model_id_expected": "lookalike-model"},
    {"model_artifact_sha256_observed": None},
    {"model_artifact_bytes_observed": True},
])
def test_signed_true_boolean_with_wrong_measured_model_identity_denied(monkeypatch, bad_fields):
    obs = observation()
    obs.update(bad_fields)
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="model_artifact_identity_not_independently_bound"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_signed_true_boolean_wrong_running_binary_sha_denied(monkeypatch):
    obs = observation()
    obs["process"]["running_executable_sha256"] = "a" * 64
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="runtime_executable_fingerprint_not_approved"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_signed_true_boolean_wrong_socat_binary_sha_denied(monkeypatch):
    obs = observation()
    obs["process"]["proxies"]["tailscale"]["running_executable_sha256"] = "f" * 64
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="transport_proxy_fingerprint_not_approved"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_missing_independent_proxy_binary_pin_denied(monkeypatch):
    doc = signed(monkeypatch)
    monkeypatch.setattr(module, "APPROVED_PROXY_EXECUTABLE_SHA256", "")
    with pytest.raises(module.WitnessHold, match="independently_approved_binary_fingerprints_missing"):
        module.verify(module.canonical(doc),
                      expected_challenge_hex=CHALLENGE, now_ms=NOW)



def test_current_boot_trust_pin_absent_in_production_source():
    source = SOURCE.read_text()
    assert 'TRUSTED_CURRENT_BOOT_ID_SHA256 = ""' in source


def test_missing_current_boot_pin_refuses_signed_receipt(monkeypatch):
    doc = signed(monkeypatch)
    monkeypatch.setattr(module, "TRUSTED_CURRENT_BOOT_ID_SHA256", "")
    with pytest.raises(module.WitnessHold, match="independent_current_boot_identity_missing"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_old_boot_signed_receipt_denied_after_external_boot_change(monkeypatch):
    doc = signed(monkeypatch)
    # The signer was approved and receipt/time/challenge still pass.
    # Only the separate trusted CURRENT-boot observation has changed.
    monkeypatch.setattr(module, "TRUSTED_CURRENT_BOOT_ID_SHA256", "f" * 64)
    with pytest.raises(module.WitnessHold, match="witness_observation_from_different_boot"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_wrong_boot_id_even_with_verified_flag_denied(monkeypatch):
    obs = observation()
    obs["process"]["boot_id_sha256"] = "f" * 64
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="witness_observation_from_different_boot"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_boot_identity_shape_still_required(monkeypatch):
    obs = observation()
    obs["process"]["boot_id_sha256"] = "not-sha256"
    doc = signed(monkeypatch, obs)
    with pytest.raises(module.WitnessHold, match="boot_identity_unverified"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)



def test_production_current_process_inventory_unconfigured():
    assert 'TRUSTED_CURRENT_PROCESS_IDENTITY = None' in SOURCE.read_text()


def test_missing_independent_current_process_inventory_denied(monkeypatch):
    doc = signed(monkeypatch)
    monkeypatch.setattr(module, "TRUSTED_CURRENT_PROCESS_IDENTITY", None)
    with pytest.raises(module.WitnessHold, match="independent_current_process_inventory_missing"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_same_boot_runtime_restart_rejects_fresh_signed_receipt(monkeypatch):
    doc = signed(monkeypatch)
    current = copy.deepcopy(module.TRUSTED_CURRENT_PROCESS_IDENTITY)
    current["runtime"]["start_ticks"] += 200
    monkeypatch.setattr(module, "TRUSTED_CURRENT_PROCESS_IDENTITY", current)
    with pytest.raises(module.WitnessHold, match="live_runtime_process_replaced_since_witness"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


@pytest.mark.parametrize("kind", ["lan", "tailscale"])
@pytest.mark.parametrize("field", ["pid", "start_ticks"])
def test_same_boot_proxy_replacement_refuses_old_signed_receipt(monkeypatch, kind, field):
    doc = signed(monkeypatch)
    current = copy.deepcopy(module.TRUSTED_CURRENT_PROCESS_IDENTITY)
    current["proxies"][kind][field] += 1
    monkeypatch.setattr(module, "TRUSTED_CURRENT_PROCESS_IDENTITY", current)
    with pytest.raises(module.WitnessHold, match="live_transport_proxy_replaced_since_witness"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


@pytest.mark.parametrize("observed", [NOW - 35_000, NOW + 1, NOW - 5_000])
def test_current_process_inventory_not_fresh_relative_to_receipt(monkeypatch, observed):
    doc = signed(monkeypatch)
    current = copy.deepcopy(module.TRUSTED_CURRENT_PROCESS_IDENTITY)
    current["observed_at_ms"] = observed
    monkeypatch.setattr(module, "TRUSTED_CURRENT_PROCESS_IDENTITY", current)
    with pytest.raises(module.WitnessHold, match="independent_current_process_inventory_not_fresh"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_current_identity_snapshot_cross_boot_denied_even_if_receipt_current(monkeypatch):
    doc = signed(monkeypatch)
    current = copy.deepcopy(module.TRUSTED_CURRENT_PROCESS_IDENTITY)
    current["boot_id_sha256"] = "f" * 64
    monkeypatch.setattr(module, "TRUSTED_CURRENT_PROCESS_IDENTITY", current)
    with pytest.raises(module.WitnessHold, match="independent_current_process_boot_mismatch"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_current_process_identity_rejects_boolean_start_ticks(monkeypatch):
    doc = signed(monkeypatch)
    current = copy.deepcopy(module.TRUSTED_CURRENT_PROCESS_IDENTITY)
    current["runtime"]["start_ticks"] = True
    monkeypatch.setattr(module, "TRUSTED_CURRENT_PROCESS_IDENTITY", current)
    with pytest.raises(module.WitnessHold, match="live_runtime_process_replaced_since_witness"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)


def test_duplicate_current_server_proxy_pid_is_rejected(monkeypatch):
    doc = signed(monkeypatch)
    obs = copy.deepcopy(doc["observation"])
    obs["process"]["proxies"]["lan"]["pid"] = obs["process"]["pid"]
    key = Ed25519PrivateKey.generate()
    # The actual separate signed identity values will be rechecked before
    # signature evaluation. No production signer is used in this fixture.
    doc, pin = fixture(private=key, obs=obs)
    monkeypatch.setattr(module, "TRUSTED_WITNESS_KEY_SHA256", pin)
    current = copy.deepcopy(module.TRUSTED_CURRENT_PROCESS_IDENTITY)
    current["proxies"]["lan"]["pid"] = current["runtime"]["pid"]
    monkeypatch.setattr(module, "TRUSTED_CURRENT_PROCESS_IDENTITY", current)
    with pytest.raises(module.WitnessHold, match="duplicate_live_server_proxy_process_identity"):
        module.verify(module.canonical(doc), expected_challenge_hex=CHALLENGE, now_ms=NOW)
