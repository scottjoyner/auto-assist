from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from assistx import mobile_agent_routes as mobile


def _app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    def auth(request: Request) -> str:
        return request.headers.get("Tailscale-User-Login") or "legacy-user"

    mobile.register_mobile_agent_routes(router, auth)
    app.include_router(router)
    return app


def test_whoami_maps_tailscale_identity(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    client = TestClient(_app())

    response = client.get(
        "/api/v1/auth/whoami",
        headers={
            "Tailscale-User-Login": "scott@example.com",
            "Tailscale-User-Name": "Scott Joyner",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["provider"] == "tailscale"
    assert payload["login"] == "scott@example.com"
    assert payload["display_name"] == "Scott Joyner"
    assert payload["account_id"].startswith("tailscale:")
    assert "scott@example.com" not in payload["account_id"]


def test_whoami_does_not_mislabel_legacy_auth_as_tailnet(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    client = TestClient(_app())

    response = client.get("/api/v1/auth/whoami")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["provider"] == "legacy"


def test_tailnet_login_allowlist_is_enforced(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "allowed@example.com")
    client = TestClient(_app())

    response = client.get(
        "/api/v1/auth/whoami",
        headers={"Tailscale-User-Login": "other@example.com"},
    )

    assert response.status_code == 403


def test_agent_chat_invokes_hermes_server_side_and_streams_openai_sse(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    captured = {}

    def fake_run(prompt: str, *, timeout: int, model):
        captured["prompt"] = prompt
        captured["timeout"] = timeout
        captured["model"] = model
        return {
            "success": True,
            "output": "Hermes completed the fleet-routed request.",
            "session_id": "server-hermes-session",
        }

    monkeypatch.setattr(mobile, "_run_hermes", fake_run)
    client = TestClient(_app())

    response = client.post(
        "/api/v1/agent/chat/completions",
        headers={
            "Tailscale-User-Login": "scott@example.com",
            "X-Hermes-Session-Id": "ios-session",
            "X-Hermes-Session-Key": "agent:main:kipnerter-ios:test",
        },
        json={
            "model": "hermes-agent",
            "stream": True,
            "messages": [
                {"role": "system", "content": "Use fleet context."},
                {"role": "user", "content": "Check the NAS recovery plan."},
            ],
        },
    )

    assert response.status_code == 200
    assert response.headers["x-kipnerter-agent-executor"] == "hermes"
    assert response.headers["x-hermes-session-id"] == "server-hermes-session"
    assert "data: " in response.text
    assert "Hermes completed the fleet-routed request." in response.text
    assert "data: [DONE]" in response.text
    assert "[SYSTEM]" in captured["prompt"]
    assert "Check the NAS recovery plan." in captured["prompt"]
    # The mobile alias selects the agent, not a raw model. The actual model is
    # chosen by Hermes/AssistX/Auto-Router on the server.
    assert captured["model"] is None


def test_agent_chat_returns_gateway_failure_when_hermes_fails(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    monkeypatch.setattr(
        mobile,
        "_run_hermes",
        lambda prompt, *, timeout, model: {
            "success": False,
            "error": "timeout",
            "output": "",
        },
    )
    client = TestClient(_app())

    response = client.post(
        "/api/v1/agent/chat/completions",
        headers={"Tailscale-User-Login": "scott@example.com"},
        json={
            "model": "hermes-agent",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["executor"] == "hermes"
