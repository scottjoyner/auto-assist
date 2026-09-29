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
                        "alias": "qwen",
                        "provider_model": "secret/model-name",
                        "model_instance_id": "model-secret-id",
                        "artifact_fingerprint": "abc123",
                        "capabilities": ["chat", "streaming", "local_only"],
                    },
                    {
                        "alias": "coder",
                        "allow_code_execution": True,
                        "capabilities": ["chat", "tools"],
                    },
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

    assert catalog["schema_version"] == "1"
    assert catalog["source"] == "assistx-runtime-projection"
    assert catalog["fleet_runtime_count"] == 1
    assert catalog["fleet_model_count"] == 2
    assert catalog["agent_runtime_count"] == 1
    assert catalog["code_runtime_count"] == 1
    assert catalog["agent_auto_available"] is True
    assert catalog["runtimes"][0]["kind"] == "lmstudio"
    assert catalog["runtimes"][0]["agent_capable"] is True
    assert catalog["runtimes"][0]["code_execution_capable"] is True
    assert catalog["runtimes"][0]["capabilities"] == ["chat", "local_only", "streaming", "tools"]
    assert catalog["runtimes"][0]["runtime_id"].startswith("runtime:")

    serialized = json.dumps(catalog, sort_keys=True)
    for forbidden in (
        "x1-370",
        "lmstudio:1234",
        "100.64.0.1",
        "secret/model-name",
        "model-secret-id",
        "artifact_fingerprint",
        "base_url",
        "access_urls",
        "node_id",
        "runtime_instance_id",
    ):
        assert forbidden not in serialized


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
