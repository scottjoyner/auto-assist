from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_NAME = "reconcile_runtime_observations"
SPEC = importlib.util.spec_from_file_location(
    MODULE_NAME,
    ROOT / "scripts" / "reconcile-runtime-observations.py",
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = module
SPEC.loader.exec_module(module)


def _projection() -> dict:
    return {
        "schema_version": "2",
        "source": "assistx",
        "generation": 8,
        "revision": "fleet-8",
        "checksum": "a" * 64,
        "signature_key_id": "projection-key-2026",
        "providers": [
            {
                "name": "assistx-destroyer-k2",
                "node_id": "destroyer",
                "runtime_instance_id": "destroyer-k2-runtime",
                "runtime_kind": "openai_compatible",
                "enabled": True,
                "base_url": "http://100.70.0.2:1235/v1",
                "access_urls": [
                    "http://192.168.1.22:1235/v1",
                    "http://100.70.0.2:1235/v1",
                ],
                "models": [
                    {
                        "alias": "k2",
                        "provider_model": "k2-36b",
                        "artifact_fingerprint": "sha256:k2",
                    }
                ],
            },
            {
                "name": "assistx-x1-lmstudio",
                "node_id": "x1-370",
                "runtime_instance_id": "x1-lmstudio",
                "runtime_kind": "lmstudio",
                "enabled": True,
                "base_url": "http://100.64.0.1:1234/v1",
                "models": [
                    {
                        "alias": "existing-model",
                        "provider_model": "existing-model",
                        "artifact_fingerprint": "sha256:existing",
                    }
                ],
            },
        ],
    }


def _nodes() -> dict:
    return {
        "nodes": [
            {
                "hostname": "destroyer",
                "source_ip": "100.70.0.2",
                "received_at": 100,
                "runtime_observations_truncated": False,
                "runtimes": [
                    {
                        "observation_schema": "fleet-runtime-observation.v1",
                        "runtime_observation_id": "runtime-observation:k2",
                        "runtime_kind": "openai_compatible",
                        "protocol": "openai-compatible",
                        "base_url": "http://localhost:1235",
                        "models": ["k2-36b"],
                        "ready": True,
                        "models_truncated": False,
                        "observed_at": 100,
                        "admitted": False,
                    },
                    {
                        "observation_schema": "fleet-runtime-observation.v1",
                        "runtime_observation_id": "runtime-observation:bonsai",
                        "runtime_kind": "openai_compatible",
                        "protocol": "openai-compatible",
                        "base_url": "http://localhost:38898",
                        "models": ["ternary-bonsai-2"],
                        "ready": True,
                        "models_truncated": False,
                        "observed_at": 100,
                        "admitted": False,
                    },
                ],
            },
            {
                "hostname": "x1-370",
                "source_ip": "100.64.0.1",
                "received_at": 100,
                "runtime_observations_truncated": False,
                "runtimes": [
                    {
                        "observation_schema": "fleet-runtime-observation.v1",
                        "runtime_observation_id": "runtime-observation:x1",
                        "runtime_kind": "lmstudio",
                        "protocol": "lmstudio-native",
                        "base_url": "http://localhost:1234",
                        "models": ["different-loaded-model"],
                        "ready": True,
                        "models_truncated": False,
                        "observed_at": 100,
                        "admitted": False,
                    }
                ],
            },
        ]
    }


def _reconcile(nodes: dict | None = None, projection: dict | None = None) -> dict:
    return module.reconcile(
        nodes or _nodes(),
        projection or _projection(),
        now_seconds=120,
        max_observation_age_seconds=60,
    )


def test_reconciliation_matches_by_node_kind_and_port_not_localhost_url() -> None:
    result = _reconcile()

    by_id = {
        item["runtime_observation_id"]: item
        for item in result["items"]
    }

    k2 = by_id["runtime-observation:k2"]
    assert k2["status"] == "projected"
    assert k2["action"] == "none"
    assert k2["node_source_match"] is True
    assert k2["fresh"] is True
    assert k2["matched_runtime_ids"] == ["destroyer-k2-runtime"]
    assert k2["artifact_identity_verified"] is False
    assert k2["projected_artifact_fingerprints"] == ["sha256:k2"]

    bonsai = by_id["runtime-observation:bonsai"]
    assert bonsai["status"] == "unprojected_runtime"
    assert bonsai["action"] == "collect_admission_evidence"
    assert "model_artifact_fingerprint" in bonsai["required_admission_evidence"]

    x1 = by_id["runtime-observation:x1"]
    assert x1["status"] == "model_drift"
    assert x1["unexpected_models"] == ["different-loaded-model"]
    assert x1["missing_projected_models"] == ["existing-model"]

    assert result["schema_version"] == "assistx.runtime-observation-reconciliation.v2"
    assert result["projection_checksum"] == "a" * 64
    assert result["projection_signature_key_id"] == "projection-key-2026"
    assert result["summary"]["projected"] == 1
    assert result["summary"]["unprojected_runtime"] == 1
    assert result["summary"]["model_drift"] == 1
    assert result["mutating"] is False
    assert result["admission_authority"] is False


def test_observation_claiming_admission_is_ignored() -> None:
    nodes = _nodes()
    nodes["nodes"][0]["runtimes"][0]["admitted"] = True

    result = _reconcile(nodes=nodes)

    ids = {
        item["runtime_observation_id"]
        for item in result["items"]
    }
    assert "runtime-observation:k2" not in ids


def test_ambiguous_same_node_port_kind_requires_identity_review() -> None:
    projection = _projection()
    duplicate = deepcopy(projection["providers"][0])
    duplicate["name"] = "duplicate"
    duplicate["runtime_instance_id"] = "destroyer-k2-duplicate"
    projection["providers"].append(duplicate)

    result = _reconcile(projection=projection)

    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )
    assert k2["status"] == "ambiguous_projection_match"
    assert k2["action"] == "review_runtime_identity"
    assert set(k2["matched_runtime_ids"]) == {
        "destroyer-k2-runtime",
        "destroyer-k2-duplicate",
    }


def test_missing_projected_model_is_model_drift() -> None:
    projection = _projection()
    projection["providers"][0]["models"].append(
        {
            "alias": "second-alias",
            "provider_model": "second-model",
            "artifact_fingerprint": "sha256:second",
        }
    )

    result = _reconcile(projection=projection)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "model_drift"
    assert k2["unexpected_models"] == []
    assert k2["missing_projected_models"] == ["second-model"]


def test_empty_or_unready_runtime_cannot_be_projected() -> None:
    nodes = _nodes()
    observation = nodes["nodes"][0]["runtimes"][0]
    observation["models"] = []
    observation["ready"] = True

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "runtime_not_ready"
    assert k2["ready"] is False
    assert "runtime_not_ready_or_no_models" in k2["reason_codes"]


def test_stale_observation_cannot_be_projected() -> None:
    nodes = _nodes()
    nodes["nodes"][0]["received_at"] = 1
    nodes["nodes"][0]["runtimes"][0]["observed_at"] = 1

    result = module.reconcile(
        nodes,
        _projection(),
        now_seconds=1000,
        max_observation_age_seconds=60,
    )
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "stale_observation"
    assert k2["fresh"] is False
    assert "observation_stale" in k2["reason_codes"]
    assert "router_receipt_stale" in k2["reason_codes"]


def test_same_node_port_with_different_kind_is_identity_mismatch() -> None:
    nodes = _nodes()
    nodes["nodes"][0]["runtimes"][0]["runtime_kind"] = "llama_cpp"

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "runtime_identity_mismatch"
    assert k2["action"] == "review_runtime_identity"
    assert "signed_provider_exists_on_node_port_with_different_runtime_kind" in k2[
        "reason_codes"
    ]


def test_transport_source_mismatch_requires_identity_review() -> None:
    nodes = _nodes()
    nodes["nodes"][0]["source_ip"] = "100.99.99.99"

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "runtime_identity_mismatch"
    assert k2["node_source_match"] is False
    assert "transport_source_ip_not_in_signed_access_paths" in k2["reason_codes"]


def test_truncated_model_evidence_is_incomplete_not_projected() -> None:
    nodes = _nodes()
    nodes["nodes"][0]["runtimes"][0]["models_truncated"] = True

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "incomplete_observation"
    assert "runtime_model_set_truncated" in k2["reason_codes"]


def test_projection_verification_state_is_explicit() -> None:
    result = module.reconcile(
        _nodes(),
        _projection(),
        now_seconds=120,
        max_observation_age_seconds=60,
        projection_verified=True,
    )
    assert result["projection_verified"] is True


def test_router_status_must_match_verified_projection() -> None:
    projection = _projection()
    status = {
        "configured": True,
        "fresh": True,
        "current": {
            "generation": projection["generation"],
            "revision": projection["revision"],
            "checksum": projection["checksum"],
            "expires_at_ms": 999_999,
            "applied_at_ms": 100,
        },
    }

    evidence = module._verify_router_status(projection, status)
    assert evidence["generation"] == projection["generation"]
    assert evidence["revision"] == projection["revision"]
    assert evidence["checksum"] == projection["checksum"]
    assert evidence["fresh"] is True


def test_router_status_mismatch_is_rejected() -> None:
    projection = _projection()
    status = {
        "configured": True,
        "fresh": True,
        "current": {
            "generation": projection["generation"],
            "revision": projection["revision"],
            "checksum": "b" * 64,
        },
    }

    with pytest.raises(ValueError, match="does not match"):
        module._verify_router_status(projection, status)


def test_missing_transport_source_cannot_be_projected() -> None:
    nodes = _nodes()
    nodes["nodes"][0]["source_ip"] = ""

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "runtime_identity_unverified"
    assert k2["node_source_match"] is None
    assert (
        "transport_source_ip_not_verifiable_against_signed_access_paths"
        in k2["reason_codes"]
    )


def _attach_verified_witness(
    nodes: dict,
    *,
    runtime_kind: str = "openai_compatible",
    model_sha: str = "sha256:k2",
    continuity_valid: bool = True,
) -> None:
    observation = nodes["nodes"][0]["runtimes"][0]
    observation["_verified_runtime_identity_witness"] = {
        "schema_version": "fleet-runtime-identity-witness.v1",
        "node_id": "destroyer",
        "runtime_url": "http://localhost:1235",
        "runtime_kind": runtime_kind,
        "provider_model": "k2-36b",
        "model_process_binding": "proc_maps",
        "model_file_identity": {
            "device": 1,
            "inode": 2,
            "size_bytes": 123,
            "mtime_ns": 456,
            "ctime_ns": 457,
        },
        "loadout_fingerprint": "sha256:" + "1" * 64,
        "model_content_sha256": model_sha,
        "witness_signing_key_fingerprint": "SHA256:witness-key",
        "canary": {
            "rollback_succeeded": True,
        },
        "process": {
            "pid": 42,
            "boot_id": "boot",
            "process_start_ticks": 99,
            "executable_basename": "llama-server",
        },
        "admission": {"admitted": False},
    }
    observation["_runtime_identity_witness_signature_verified"] = True
    observation["runtime_identity_continuity"] = {
        "valid": continuity_valid,
        "reason": "match" if continuity_valid else "process_identity_changed",
        "checked_at": 110,
        "pid": 42,
        "boot_id": "boot",
        "process_start_ticks": 99,
        "executable_basename": "llama-server",
        "model_file_valid": True,
        "model_process_binding_valid": True,
        "model_process_binding": "proc_maps",
    }


def test_signed_witness_upgrades_projected_to_artifact_and_process_identity() -> None:
    nodes = _nodes()
    _attach_verified_witness(nodes)

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "projected"
    assert k2["artifact_identity_verified"] is True
    assert k2["artifact_identity_reason"] == "signed_model_artifact_and_process_match"
    assert k2["identity_evidence_level"] == "signed_model_artifact_and_process"
    assert k2["witness_model_content_sha256"] == "sha256:k2"
    assert k2["witness_signing_key_fingerprint"] == "SHA256:witness-key"


def test_signed_witness_artifact_mismatch_becomes_model_drift() -> None:
    nodes = _nodes()
    _attach_verified_witness(nodes, model_sha="sha256:different")

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "model_drift"
    assert k2["artifact_identity_verified"] is False
    assert "signed_witness_artifact_fingerprint_mismatch" in k2["reason_codes"]


def test_signed_witness_process_change_becomes_identity_mismatch() -> None:
    nodes = _nodes()
    _attach_verified_witness(nodes, continuity_valid=False)

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "runtime_identity_mismatch"
    assert k2["artifact_identity_verified"] is False
    assert "signed_witness_process_continuity_failed" in k2["reason_codes"]


def test_unverified_witness_signature_cannot_upgrade_identity() -> None:
    nodes = _nodes()
    observation = nodes["nodes"][0]["runtimes"][0]
    observation["_runtime_identity_witness_error"] = "bad signature"

    result = _reconcile(nodes=nodes)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["status"] == "runtime_identity_unverified"
    assert k2["artifact_identity_verified"] is False
    assert "runtime_identity_witness_signature_unverified" in k2["reason_codes"]


def test_signed_witness_can_refine_generic_runtime_kind() -> None:
    nodes = _nodes()
    projection = _projection()
    projection["providers"][0]["runtime_kind"] = "llama_cpp"
    _attach_verified_witness(nodes, runtime_kind="llama_cpp")

    result = _reconcile(nodes=nodes, projection=projection)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["observed_runtime_kind"] == "openai_compatible"
    assert k2["runtime_kind"] == "llama_cpp"
    assert k2["runtime_kind_refined_by_signed_witness"] is True
    assert k2["status"] == "projected"
    assert k2["artifact_identity_verified"] is True


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="OpenSSH unavailable")
def test_runtime_witness_signature_verification_round_trip(tmp_path) -> None:
    key = tmp_path / "witness-key"
    generated = subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        capture_output=True,
        check=False,
    )
    assert generated.returncode == 0
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(
        "runtime-witness-operator "
        + key.with_suffix(".pub").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    core = {
        "schema_version": "fleet-runtime-identity-witness.v1",
        "node_id": "destroyer",
        "runtime_url": "http://localhost:1235",
        "runtime_kind": "llama_cpp",
        "provider_model": "k2-36b",
        "model_process_binding": "proc_maps",
        "model_file_identity": {
            "device": 1,
            "inode": 2,
            "size_bytes": 123,
            "mtime_ns": 456,
            "ctime_ns": 457,
        },
        "loadout_fingerprint": "sha256:" + "1" * 64,
        "model_content_sha256": "sha256:" + "2" * 64,
        "witness_signing_key_fingerprint": "SHA256:witness",
        "canary": {"rollback_succeeded": True},
        "process": {
            "pid": 42,
            "boot_id": "boot",
            "process_start_ticks": 99,
            "executable_basename": "llama-server",
        },
        "admission": {"admitted": False},
    }
    document = {
        **core,
        "witness_fingerprint": module._canonical_hash(core),
    }
    payload = module._canonical_witness_bytes(document)
    source = tmp_path / "witness.json"
    source.write_bytes(payload)
    signed = subprocess.run(
        [
            "ssh-keygen",
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            "lms-runtime-identity-witness",
            str(source),
        ],
        capture_output=True,
        check=False,
    )
    assert signed.returncode == 0
    signature = Path(str(source) + ".sig").read_text(encoding="utf-8")

    verified = module._verify_runtime_identity_witness(
        payload.decode("utf-8"),
        signature,
        allowed_signers=allowed,
        identity="runtime-witness-operator",
    )

    assert verified == document


def test_unverified_generic_witness_does_not_look_unprojected() -> None:
    nodes = _nodes()
    projection = _projection()
    projection["providers"][0]["runtime_kind"] = "llama_cpp"
    observation = nodes["nodes"][0]["runtimes"][0]
    observation["_runtime_identity_witness_error"] = "bad signature"

    result = _reconcile(nodes=nodes, projection=projection)
    k2 = next(
        item
        for item in result["items"]
        if item["runtime_observation_id"] == "runtime-observation:k2"
    )

    assert k2["observed_runtime_kind"] == "openai_compatible"
    assert k2["status"] == "runtime_identity_unverified"
    assert k2["action"] == "review_runtime_identity"
    assert "runtime_identity_witness_signature_unverified" in k2["reason_codes"]
    assert k2["matched_runtime_ids"] == ["destroyer-k2-runtime"]
