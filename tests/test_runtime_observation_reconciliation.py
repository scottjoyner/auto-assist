from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from pathlib import Path


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
