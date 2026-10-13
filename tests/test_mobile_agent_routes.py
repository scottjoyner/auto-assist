from __future__ import annotations

import sys
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
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "scott@example.com")
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


def test_unconfigured_tailnet_allowlist_rejects_every_login(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    client = TestClient(_app())
    for value in (None, "", "  ,  "):
        if value is None:
            monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
        else:
            monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", value)
        response = client.get(
            "/api/v1/auth/whoami",
            headers={"Tailscale-User-Login": "scott@example.com"},
        )
        assert response.status_code == 403


def test_file_backed_tailnet_allowlist_supports_existing_container_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.delenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", raising=False)
    monkeypatch.setenv("ASSISTX_FLEET_STATE_DIR", str(tmp_path))
    (tmp_path / "kipnerter-mobile-allowlist.txt").write_text(
        "scott@example.com\n", encoding="utf-8"
    )
    client = TestClient(_app())
    allowed = client.get(
        "/api/v1/auth/whoami",
        headers={"Tailscale-User-Login": "scott@example.com"},
    )
    denied = client.get(
        "/api/v1/auth/whoami",
        headers={"Tailscale-User-Login": "other@example.com"},
    )
    assert allowed.status_code == 200
    assert denied.status_code == 403


def test_environment_allowlist_takes_precedence_over_file(monkeypatch, tmp_path):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "env@example.com")
    monkeypatch.setenv("ASSISTX_FLEET_STATE_DIR", str(tmp_path))
    (tmp_path / "kipnerter-mobile-allowlist.txt").write_text(
        "file@example.com\n", encoding="utf-8"
    )
    client = TestClient(_app())
    assert client.get(
        "/api/v1/auth/whoami",
        headers={"Tailscale-User-Login": "env@example.com"},
    ).status_code == 200
    assert client.get(
        "/api/v1/auth/whoami",
        headers={"Tailscale-User-Login": "file@example.com"},
    ).status_code == 403


def test_executor_security_does_not_break_tailnet_mobile_auth(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "scott@example.com")
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
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "scott@example.com")
    app, _ = _executor_wrapped_app()

    response = TestClient(app).get(
        "/api/v1/auth/whoami",
        headers={"x-assistx-executor-identity": "spoofed-executor"},
    )

    assert response.status_code == 401


def test_non_tailscale_trusted_header_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "x-assistx-executor-identity")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "scott@example.com")
    client = TestClient(_app())

    response = client.get(
        "/api/v1/auth/whoami",
        headers={"Tailscale-User-Login": "scott@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["authenticated"] is False
    assert response.json()["provider"] == "legacy"


def test_current_runtime_projection_calls_real_v2_export_name(monkeypatch):
    marker = object()
    captured = {}

    def build_runtime_projection(neo_factory, *, ttl_seconds):
        captured["neo_factory"] = neo_factory
        captured["ttl_seconds"] = ttl_seconds
        return {"schema_version": "2", "expires_at_ms": 9_999_999_999_999}

    monkeypatch.setitem(sys.modules, "assistx.api", SimpleNamespace(_neo=marker))
    monkeypatch.setitem(
        sys.modules,
        "assistx.runtime_projection_v2",
        SimpleNamespace(build_runtime_projection=build_runtime_projection),
    )
    monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_TTL_SECONDS", "900")

    projection = mobile._current_runtime_projection()

    assert projection["schema_version"] == "2"
    assert captured == {"neo_factory": marker, "ttl_seconds": 900}


def test_agent_chat_invokes_hermes_server_side_and_streams_openai_sse(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "scott@example.com")
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
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "scott@example.com")
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
    assert response.json()["detail"]["error"] == "timeout"


def test_agent_chat_maps_projection_failure_to_stable_mobile_error(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_HEADER", "Tailscale-User-Login")
    monkeypatch.setenv("KIPNERTER_TAILNET_ALLOWED_LOGINS", "scott@example.com")
    monkeypatch.setattr(
        mobile,
        "_run_hermes",
        lambda prompt, *, timeout, model, provider: {
            "success": False,
            "error": "exit_code_1",
            "output": (
                'API call failed: {"detail":{"error":"all providers failed",'
                '"details":["signed AssistX runtime projection is not configured"]}}'
            ),
            "stderr": "internal executor trace that must not reach mobile",
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
    assert response.json() == {
        "detail": {
            "error": "runtime_projection_unavailable",
            "executor": "hermes",
        }
    }
    assert "signed AssistX" not in response.text
    assert "internal executor trace" not in response.text
