from fastapi.testclient import TestClient
import pytest

from assistx import overlay as overlay_mod


def test_overlay_configuration_defaults_to_direct(monkeypatch):
    monkeypatch.delenv("ASSISTX_OVERLAY_MODE", raising=False)
    monkeypatch.delenv("AUTO_ROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("AUTO_ASSIGN_BASE_URL", raising=False)

    config = overlay_mod.build_overlay_configuration()

    assert config["ok"] is True
    assert config["mode"] == "direct"
    assert config["services"]["auto_router"]["required"] is False
    assert config["services"]["auto_assign"]["required"] is False


def test_overlay_configuration_requires_router_url_when_enabled(monkeypatch):
    monkeypatch.setenv("ASSISTX_OVERLAY_MODE", "router")
    monkeypatch.delenv("AUTO_ROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("AUTO_ASSIGN_BASE_URL", raising=False)

    config = overlay_mod.build_overlay_configuration()

    assert config["ok"] is False
    assert any(item["field"] == "AUTO_ROUTER_BASE_URL" for item in config["issues"])


def test_overlay_status_endpoint_reports_services(monkeypatch):
    pytest.importorskip("langgraph")
    from assistx.api_router import app

    monkeypatch.setenv("ASSISTX_OVERLAY_MODE", "router_plus_assign")
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://router.local:8088")
    monkeypatch.setenv("AUTO_ASSIGN_BASE_URL", "http://assign.local:8090")

    class _Response:
        ok = True
        status_code = 200

        def __init__(self, payload):
            self._payload = payload
            self.text = "ok"

        def json(self):
            return self._payload

    def fake_get(url, timeout):
        if "8088" in url:
            return _Response({"status": "ok", "service": "auto-router"})
        return _Response({"status": "ok", "service": "auto-assign"})

    monkeypatch.setattr(overlay_mod.requests, "get", fake_get)

    client = TestClient(app)
    response = client.get("/api/overlay/status")

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "router_plus_assign"
    assert body["services"]["auto_router"]["status"] == "ok"
    assert body["services"]["auto_assign"]["status"] == "ok"
    assert body["configuration"]["ok"] is True


def test_direct_mode_ignores_invalid_overlay_urls(monkeypatch):
    monkeypatch.setenv("ASSISTX_OVERLAY_MODE", "direct")
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "not-a-url")
    monkeypatch.setenv("AUTO_ASSIGN_BASE_URL", "also-not-a-url")

    config = overlay_mod.build_overlay_configuration()

    assert config["ok"] is True
    assert config["issues"] == []
    assert config["services"]["auto_router"]["enabled"] is False
    assert config["services"]["auto_assign"]["enabled"] is False


def test_direct_mode_health_reports_router_and_assign_disabled(monkeypatch):
    monkeypatch.delenv("ASSISTX_OVERLAY_MODE", raising=False)
    monkeypatch.delenv("AUTO_ROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("AUTO_ASSIGN_BASE_URL", raising=False)

    health = overlay_mod.build_overlay_health()

    assert health["ok"] is True
    assert health["mode"] == "direct"
    assert health["services"]["auto_router"]["status"] == "disabled"
    assert health["services"]["auto_assign"]["status"] == "disabled"
