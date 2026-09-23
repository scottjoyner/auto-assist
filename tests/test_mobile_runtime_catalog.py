from __future__ import annotations

import json

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from assistx import mobile_agent_routes as mobile


def _projection() -> dict:
    return {
        "generated_at_ms": 100,
        "expires_at_ms": 200,
        "providers": [
            {
                "name": "assistx-secret-node-runtime",
                "node_id": "x1-370",
                "runtime_instance_id": "lmstudio:1234",
                "runtime_kind": "lmstudio",
                "enabled": True,
                "base_url": "http://100.64.0.1:1234/v1",
                "access_urls": ["http://x1-370:1234/v1"],
                "allow_agent_runtime": True,
                "allow_code_execution": False,
                "models": [
                    {
                        "alias": "Qwen 35B",
                        "provider_model": "secret/model-name",
                        "model_instance_id": "model-secret-id",
                        "artifact_fingerprint": "sha256:qwen-artifact",
                        "capabilities": ["chat", "streaming", "local_only"],
                    },
                    {
                        "alias": "Coder 7B",
                        "provider_model": "internal/coder-route",
                        "model_instance_id": "coder-secret-id",
                        "artifact_fingerprint": "sha256:coder-artifact",
                        "allow_code_execution": True,
                        "capabilities": ["chat", "tools"],
                    },
                ],
            },
            {
                "name": "assistx-second-secret-runtime",
                "node_id": "destroyer",
                "runtime_instance_id": "llama:38898",
                "runtime_kind": "llama_cpp",
                "enabled": True,
                "base_url": "http://destroyer:38898/v1",
                "access_urls": ["http://100.70.0.2:38898/v1"],
                "models": [
                    {
                        "alias": "Qwen 35B",
                        "provider_model": "different/internal-route",
                        "model_instance_id": "replica-secret-id",
                        "artifact_fingerprint": "sha256:qwen-artifact",
                        "capabilities": ["chat", "streaming"],
                    }
                ],
            },
            {
                "name": "ignored-empty-runtime",
                "node_id": "empty-node",
                "runtime_instance_id": "empty",
                "runtime_kind": "vllm",
                "models": [],
            },
        ],
    }


def test_mobile_catalog_redacts_internal_runtime_coordinates():
    catalog = mobile._sanitize_runtime_projection_for_mobile(_projection())

    assert catalog["schema_version"] == "2"
    assert catalog["source"] == "assistx-runtime-projection"
    assert catalog["fleet_runtime_count"] == 2
    assert catalog["fleet_model_count"] == 3
    assert catalog["fleet_unique_model_count"] == 2
    assert catalog["agent_runtime_count"] == 1
    assert catalog["code_runtime_count"] == 1
    assert catalog["agent_auto_available"] is True

    assert [runtime["kind"] for runtime in catalog["runtimes"]] == [
        "llama_cpp",
        "lmstudio",
    ]
    assert all(runtime["runtime_id"].startswith("runtime:") for runtime in catalog["runtimes"])

    models = {item["display_name"]: item for item in catalog["models"]}
    assert set(models) == {"Coder 7B", "Qwen 35B"}
    assert models["Qwen 35B"]["model_handle"].startswith("model:")
    assert models["Qwen 35B"]["state"] == "ready"
    assert models["Qwen 35B"]["ready_runtime_count"] == 2
    assert models["Qwen 35B"]["capabilities"] == [
        "chat",
        "local_only",
        "streaming",
    ]
    assert models["Coder 7B"]["ready_runtime_count"] == 1
    assert models["Coder 7B"]["code_execution_capable"] is True

    serialized = json.dumps(catalog, sort_keys=True)
    for forbidden in (
        "x1-370",
        "destroyer",
        "lmstudio:1234",
        "llama:38898",
        "100.64.0.1",
        "100.70.0.2",
        "secret/model-name",
        "different/internal-route",
        "internal/coder-route",
        "model-secret-id",
        "coder-secret-id",
        "replica-secret-id",
        "sha256:qwen-artifact",
        "sha256:coder-artifact",
        "artifact_fingerprint",
        "provider_model",
        "model_instance_id",
        "base_url",
        "access_urls",
        "node_id",
        "runtime_instance_id",
    ):
        assert forbidden not in serialized


def test_mobile_model_handle_survives_runtime_migration():
    first = {
        "artifact_fingerprint": "sha256:same-artifact",
        "provider_model": "route-a",
        "alias": "Ternary Bonsai 2",
    }
    moved = {
        "artifact_fingerprint": "sha256:same-artifact",
        "provider_model": "route-b",
        "alias": "Ternary Bonsai 2",
    }
    other = {
        "artifact_fingerprint": "sha256:different-artifact",
        "provider_model": "route-c",
        "alias": "Ternary Bonsai 2",
    }

    assert mobile._mobile_model_handle(first) == mobile._mobile_model_handle(moved)
    assert mobile._mobile_model_handle(first) != mobile._mobile_model_handle(other)
    assert mobile._mobile_model_handle({"alias": "missing identity"}) is None


def test_mobile_catalog_omits_unidentified_models_from_handle_list_but_keeps_counts():
    projection = _projection()
    projection["providers"][0]["models"].append(
        {
            "alias": "Legacy Unknown",
            "capabilities": ["chat"],
        }
    )

    catalog = mobile._sanitize_runtime_projection_for_mobile(projection)

    assert catalog["fleet_model_count"] == 4
    assert catalog["fleet_unique_model_count"] == 2
    assert "Legacy Unknown" not in {
        item["display_name"] for item in catalog["models"]
    }


def _app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    def auth(request: Request, credentials=None) -> str:
        login = request.headers.get("Tailscale-User-Login")
        if not login:
            raise AssertionError("test expected Tailnet identity")
        return login

    mobile.register_mobile_agent_routes(router, auth)
    app.include_router(router)
    return app


def test_mobile_runtime_catalog_route_uses_tailnet_boundary(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setattr(
        mobile,
        "_mobile_runtime_catalog",
        lambda: mobile._sanitize_runtime_projection_for_mobile(_projection()),
    )

    response = TestClient(_app()).get(
        "/api/v1/runtime/catalog",
        headers={"Tailscale-User-Login": "scott@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["fleet_runtime_count"] == 1


def test_mobile_runtime_catalog_route_sanitizes_backend_failure(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")

    def fail():
        raise RuntimeError("bolt://neo4j:7687 super-secret-detail")

    monkeypatch.setattr(mobile, "_mobile_runtime_catalog", fail)
    response = TestClient(_app()).get(
        "/api/v1/runtime/catalog",
        headers={"Tailscale-User-Login": "scott@example.com"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": {"error": "runtime_catalog_unavailable"}}
    assert "neo4j" not in response.text.lower()
