from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.testclient import TestClient

from assistx import executor_security
from assistx import mobile_agent_routes as mobile


def _app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    def auth(
        request: Request,
        credentials: HTTPBasicCredentials | None = None,
    ) -> str:
        return request.headers.get("Tailscale-User-Login") or "legacy-user"

    mobile.register_mobile_agent_routes(router, auth)
    app.include_router(router)
    return app


def _executor_wrapped_app() -> tuple[FastAPI, SimpleNamespace]:
    """Reproduce production's shared-auth + executor-security interaction."""

    app = FastAPI()
    router = APIRouter()
    security = HTTPBasic(auto_error=False)
    auth_module = SimpleNamespace(TRUSTED_AUTH_HEADER="Tailscale-User-Login")

    def shared_auth(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(security),
    ) -> str:
        trusted_header = str(auth_module.TRUSTED_AUTH_HEADER or "").strip()
        if trusted_header:
            trusted_user = request.headers.get(trusted_header)
            if trusted_user:
                return trusted_user
        if credentials and credentials.username == "admin" and credentials.password == "secret":
            return credentials.username
        raise HTTPException(status_code=401, detail="Unauthorized")

    mobile.register_mobile_agent_routes(router, shared_auth)
    app.include_router(router)
    executor_security.install_executor_security(app, lambda: None, auth_module)
    return app, auth_module


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


def test_executor_security_does_not_break_tailnet_mobile_auth(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    app, auth_module = _executor_wrapped_app()

    # Production executor security deliberately takes ownership of the legacy
    # module-level trusted-header slot for its internal executor identity.
    assert auth_module.TRUSTED_AUTH_HEADER == "x-assistx-executor-identity"

    response = TestClient(app).get(
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


def test_executor_internal_identity_header_cannot_authenticate_mobile_route(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    app, _ = _executor_wrapped_app()

    response = TestClient(app).get(
        "/api/v1/auth/whoami",
        headers={"x-assistx-executor-identity": "spoofed-executor"},
    )

    assert response.status_code == 401


def test_non_tailscale_trusted_header_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "x-assistx-executor-identity")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    client = TestClient(_app())

    response = client.get(
        "/api/v1/auth/whoami",
        headers={"Tailscale-User-Login": "scott@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["provider"] == "legacy"


def test_agent_chat_invokes_hermes_server_side_and_streams_openai_sse(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    captured = {}

    def fake_run(prompt: str, *, timeout: int, model, provider):
        captured["prompt"] = prompt
        captured["timeout"] = timeout
        captured["model"] = model
        captured["provider"] = provider
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
    assert captured["provider"] == "assistx-router"


def test_agent_chat_returns_gateway_failure_when_hermes_fails(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    monkeypatch.setattr(
        mobile,
        "_run_hermes",
        lambda prompt, *, timeout, model, provider: {
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
