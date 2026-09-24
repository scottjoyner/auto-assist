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
    catalog = mobile._sanitize_runtime_projection_for_mobile(
        _projection(),
        handle_secret="test-mobile-handle-secret",
    )

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
    assert models["Qwen 35B"]["model_handle"].startswith("model:v1:")
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

    secret = "test-mobile-handle-secret"
    assert mobile._mobile_model_handle(first, secret=secret) == mobile._mobile_model_handle(moved, secret=secret)
    assert mobile._mobile_model_handle(first, secret=secret) != mobile._mobile_model_handle(other, secret=secret)
    assert mobile._mobile_model_handle(first, secret="different-secret") != mobile._mobile_model_handle(first, secret=secret)
    assert mobile._mobile_model_handle({"alias": "missing identity"}, secret=secret) is None
    assert mobile._mobile_model_handle(first, secret="") is None


def test_mobile_catalog_omits_unidentified_models_from_handle_list_but_keeps_counts():
    projection = _projection()
    projection["providers"][0]["models"].append(
        {
            "alias": "Legacy Unknown",
            "capabilities": ["chat"],
        }
    )

    catalog = mobile._sanitize_runtime_projection_for_mobile(
        projection,
        handle_secret="test-mobile-handle-secret",
    )

    assert catalog["fleet_model_count"] == 4
    assert catalog["fleet_unique_model_count"] == 2
    assert "Legacy Unknown" not in {
        item["display_name"] for item in catalog["models"]
    }


def test_model_handle_resolves_current_projection_across_replicas():
    projection = _projection()
    projection["expires_at_ms"] = 9_999_999_999_999
    secret = "test-mobile-handle-secret"
    handle = mobile._mobile_model_handle(
        projection["providers"][0]["models"][0],
        secret=secret,
    )
    assert handle is not None

    resolved = mobile._resolve_mobile_model_handle(
        projection,
        handle,
        handle_secret=secret,
    )

    assert resolved is not None
    assert resolved["artifact_fingerprint"] == "sha256:qwen-artifact"
    assert resolved["ready_runtime_count"] == 2
    assert resolved["model_handle"] == handle
    assert resolved["capabilities"] == ["chat", "local_only", "streaming"]



def test_model_handle_survives_replica_loss_with_same_artifact_authority():
    projection = _projection()
    projection["expires_at_ms"] = 9_999_999_999_999
    secret = "test-mobile-handle-secret"
    handle = mobile._mobile_model_handle(
        projection["providers"][0]["models"][0],
        secret=secret,
    )
    assert handle is not None

    before = mobile._resolve_mobile_model_handle(
        projection,
        handle,
        handle_secret=secret,
    )
    assert before is not None
    assert before["model_handle"] == handle
    assert before["artifact_fingerprint"] == "sha256:qwen-artifact"
    assert before["ready_runtime_count"] == 2

    # Simulate the LM Studio replica disappearing while the llama.cpp replica
    # for the same admitted artifact remains in the fresh signed projection.
    surviving_projection = {
        **projection,
        "providers": [
            provider
            for provider in projection["providers"]
            if provider.get("name") != "assistx-secret-node-runtime"
        ],
    }
    after = mobile._resolve_mobile_model_handle(
        surviving_projection,
        handle,
        handle_secret=secret,
    )

    assert after is not None
    assert after["model_handle"] == handle
    assert after["artifact_fingerprint"] == before["artifact_fingerprint"]
    assert after["ready_runtime_count"] == 1
    assert after["display_name"] == "Qwen 35B"

    body = mobile.MobileModelChatIn(
        model_handle=handle,
        messages=[mobile.MobileAgentMessageIn(role="user", content="hello")],
        stream=False,
    )
    routed = mobile._mobile_model_router_payload(body, after)
    assert routed["metadata"]["assistx_artifact_fingerprint"] == "sha256:qwen-artifact"
    assert routed["metadata"]["assistx_mobile_model_handle"] == handle
    assert "runtime_instance_id" not in json.dumps(routed)
    assert "base_url" not in json.dumps(routed)

def test_model_handle_resolution_fails_closed_for_unknown_or_expired():
    projection = _projection()
    projection["expires_at_ms"] = 9_999_999_999_999
    secret = "test-mobile-handle-secret"

    assert mobile._resolve_mobile_model_handle(
        projection,
        "model:v1:" + "0" * 32,
        handle_secret=secret,
    ) is None

    model = projection["providers"][0]["models"][0]
    handle = mobile._mobile_model_handle(model, secret=secret)
    projection["expires_at_ms"] = 1
    assert mobile._resolve_mobile_model_handle(
        projection,
        handle,
        handle_secret=secret,
    ) is None


def test_mobile_model_router_payload_carries_internal_artifact_not_route():
    body = mobile.MobileModelChatIn(
        model_handle="model:v1:" + "a" * 32,
        messages=[
            mobile.MobileAgentMessageIn(role="user", content="hello")
        ],
        stream=False,
    )
    resolved = {
        "artifact_fingerprint": "sha256:artifact",
        "display_name": "Bonsai",
    }

    payload = mobile._mobile_model_router_payload(body, resolved)

    assert payload["model"] == "auto/local"
    assert payload["local_only"] is True
    assert payload["allow_cloud"] is False
    assert payload["metadata"]["privacy"] == "local_only"
    assert payload["metadata"]["assistx_artifact_fingerprint"] == "sha256:artifact"
    assert "base_url" not in json.dumps(payload)
    assert "runtime_instance_id" not in json.dumps(payload)


def test_router_completion_sanitizer_replaces_backend_identity():
    sanitized = mobile._sanitize_router_completion(
        {
            "id": "provider-id",
            "object": "chat.completion",
            "model": "secret-provider-model",
            "system_fingerprint": "backend-fingerprint",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 3},
        },
        "model:v1:" + "b" * 32,
    )

    assert sanitized["model"] == "model:v1:" + "b" * 32
    assert "system_fingerprint" not in sanitized
    assert "secret-provider-model" not in json.dumps(sanitized)


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
        lambda: mobile._sanitize_runtime_projection_for_mobile(
            _projection(),
            handle_secret="test-mobile-handle-secret",
        ),
    )

    response = TestClient(_app()).get(
        "/api/v1/runtime/catalog",
        headers={"Tailscale-User-Login": "scott@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["fleet_runtime_count"] == 2


def test_mobile_model_chat_resolves_handle_and_sanitizes_router_response(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv(
        "KIPNERTER_MOBILE_MODEL_HANDLE_SECRET",
        "test-mobile-handle-secret",
    )
    monkeypatch.setenv("FLEET_ROUTER_URL", "http://router:8088")
    monkeypatch.setenv("FLEET_ROUTER_BEARER_TOKEN", "server-only-token")

    projection = _projection()
    projection["expires_at_ms"] = 9_999_999_999_999
    monkeypatch.setattr(
        mobile,
        "_current_runtime_projection",
        lambda: projection,
    )
    handle = mobile._mobile_model_handle(
        projection["providers"][0]["models"][0],
        secret="test-mobile-handle-secret",
    )
    assert handle is not None

    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "id": "backend-id",
                "object": "chat.completion",
                "model": "secret-provider-model",
                "system_fingerprint": "secret-fingerprint",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "fleet reply",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(mobile.httpx, "AsyncClient", FakeAsyncClient)

    response = TestClient(_app()).post(
        "/api/v1/model/chat/completions",
        headers={"Tailscale-User-Login": "scott@example.com"},
        json={
            "model_handle": handle,
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == handle
    assert response.json()["choices"][0]["message"]["content"] == "fleet reply"
    assert "secret-provider-model" not in response.text
    assert "secret-fingerprint" not in response.text
    assert captured["url"] == "http://router:8088/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer server-only-token"
    assert captured["payload"]["metadata"]["assistx_artifact_fingerprint"] == "sha256:qwen-artifact"
    assert captured["payload"]["local_only"] is True


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
