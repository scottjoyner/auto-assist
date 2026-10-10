from __future__ import annotations

from fastapi.testclient import TestClient

from assistx.mobile_trace_context import validated_traceparent
from assistx import mobile_agent_routes as mobile
from test_mobile_agent_routes import _app

TRACE = "00-1234567890abcdef1234567890abcdef-0123456789abcdef-01"


def test_w3c_traceparent_validates_without_becoming_authority():
    assert validated_traceparent(TRACE) == TRACE
    for value in (
        "",
        "01-1234567890abcdef1234567890abcdef-0123456789abcdef-01",
        "00-" + "0" * 32 + "-0123456789abcdef-01",
        "00-1234567890abcdef1234567890abcdef-" + "0" * 16 + "-01",
        TRACE + "\r\nAuthorization: Bearer secret",
        "00-Z234567890abcdef1234567890abcdef-0123456789abcdef-01",
    ):
        assert validated_traceparent(value) is None


def test_mobile_agent_echoes_valid_trace_without_accepting_invalid(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setattr(mobile, "_run_hermes", lambda *a, **kw: {
        "success": True, "output": "READY", "session_id": "test-session",
    })
    client = TestClient(_app())
    body = {"model": "hermes-agent", "messages": [{"role": "user", "content": "Go"}], "stream": False}
    headers = {"Tailscale-User-Login": "scott@example.com", "traceparent": TRACE}
    response = client.post("/api/v1/agent/chat/completions", json=body, headers=headers)
    assert response.status_code == 200
    assert response.headers.get("traceparent") == TRACE
    bad = dict(headers, traceparent=TRACE[:-1] + "x")
    response = client.post("/api/v1/agent/chat/completions", json=body, headers=bad)
    assert response.status_code == 200
    assert "traceparent" not in response.headers

def test_mobile_model_trace_correlates_router_request_without_changing_authority(monkeypatch):
    from test_mobile_runtime_catalog import _projection

    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_MOBILE_MODEL_HANDLE_SECRET", "test-mobile-handle-secret")
    monkeypatch.setenv("FLEET_ROUTER_URL", "http://router:8088")
    monkeypatch.setenv("FLEET_ROUTER_BEARER_TOKEN", "server-only-token")
    projection = _projection()
    projection["expires_at_ms"] = 9_999_999_999_999
    monkeypatch.setattr(mobile, "_current_runtime_projection", lambda: projection)
    handle = mobile._mobile_model_handle(
        projection["providers"][0]["models"][0], secret="test-mobile-handle-secret"
    )
    captured = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                             "finish_reason": "stop"}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, url, *, headers, json):
            captured.append(dict(headers))
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(mobile.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(_app())
    body = {
        "model_handle": handle,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    headers = {"Tailscale-User-Login": "scott@example.com", "traceparent": TRACE}
    good = client.post("/api/v1/model/chat/completions", headers=headers, json=body)
    assert good.status_code == 200
    assert good.headers["traceparent"] == TRACE
    assert captured[-1]["traceparent"] == TRACE
    assert captured[-1]["Authorization"] == "Bearer server-only-token"
    assert good.headers["x-kipnerter-model-authority"] == "assistx-runtime-projection"

    bad = client.post("/api/v1/model/chat/completions",
                      headers=dict(headers, traceparent="garbage"),
                      json=body)
    assert bad.status_code == 200
    assert "traceparent" not in bad.headers
    assert "traceparent" not in captured[-1]
