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
