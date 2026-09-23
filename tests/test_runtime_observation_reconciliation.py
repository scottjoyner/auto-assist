from __future__ import annotations

import importlib.util
import sys
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
                        "alias": "k2-36b",
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
                "runtimes": [
                    {
                        "observation_schema": "fleet-runtime-observation.v1",
                        "runtime_observation_id": "runtime-observation:k2",
                        "runtime_kind": "openai_compatible",
                        "protocol": "openai-compatible",
                        "base_url": "http://localhost:1235",
                        "models": ["k2-36b"],
                        "ready": True,
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
                        "observed_at": 100,
                        "admitted": False,
                    },
                ],
            },
            {
                "hostname": "x1-370",
                "runtimes": [
                    {
                        "observation_schema": "fleet-runtime-observation.v1",
                        "runtime_observation_id": "runtime-observation:x1",
                        "runtime_kind": "lmstudio",
                        "protocol": "lmstudio-native",
                        "base_url": "http://localhost:1234",
                        "models": ["different-loaded-model"],
                        "ready": True,
                        "observed_at": 100,
                        "admitted": False,
                    }
                ],
            },
        ]
    }


def test_reconciliation_matches_by_node_kind_and_port_not_localhost_url() -> None:
    result = module.reconcile(_nodes(), _projection())

    by_id = {
        item["runtime_observation_id"]: item
        for item in result["items"]
    }

    assert by_id["runtime-observation:k2"]["status"] == "projected"
    assert by_id["runtime-observation:k2"]["action"] == "none"
    assert by_id["runtime-observation:k2"]["matched_runtime_ids"] == [
        "destroyer-k2-runtime"
    ]

    bonsai = by_id["runtime-observation:bonsai"]
    assert bonsai["status"] == "unprojected_runtime"
    assert bonsai["action"] == "collect_admission_evidence"
    assert "model_artifact_fingerprint" in bonsai[
        "required_admission_evidence"
    ]

    x1 = by_id["runtime-observation:x1"]
    assert x1["status"] == "model_drift"
    assert x1["missing_models"] == ["different-loaded-model"]

    assert result["summary"]["projected"] == 1
    assert result["summary"]["unprojected_runtime"] == 1
    assert result["summary"]["model_drift"] == 1
    assert result["mutating"] is False
    assert result["admission_authority"] is False


def test_observation_claiming_admission_is_ignored() -> None:
    nodes = _nodes()
    nodes["nodes"][0]["runtimes"][0]["admitted"] = True

    result = module.reconcile(nodes, _projection())

    ids = {
        item["runtime_observation_id"]
        for item in result["items"]
    }
    assert "runtime-observation:k2" not in ids


def test_ambiguous_same_node_port_kind_requires_identity_review() -> None:
    projection = _projection()
    duplicate = dict(projection["providers"][0])
    duplicate["name"] = "duplicate"
    duplicate["runtime_instance_id"] = "destroyer-k2-duplicate"
    projection["providers"].append(duplicate)

    result = module.reconcile(_nodes(), projection)

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
