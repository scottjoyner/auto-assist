"""Hermetic regression: router health must affirmatively report ok=true.

No live router, HTTP endpoint, DB, credential or docker daemon is used. The
only inputs are synthetic JSON payloads fed through a stubbed ``_http_json``.
"""

from __future__ import annotations

import pytest

from assistx import control_room


def _router_dependency(monkeypatch, health_payload):
    monkeypatch.setenv("AUTO_ROUTER_BASE_URL", "http://router.invalid:8088")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "synthetic-admin-token")

    def fake_http_json(base_url, path, *, timeout=2.0, admin_token=""):
        if path == "/health":
            return health_payload, 1.25
        return {"runtimes": [], "access_paths": []}, 0.5

    monkeypatch.setattr(control_room, "_http_json", fake_http_json)
    dependencies, _admission = control_room._probe_router()
    router_deps = [item for item in dependencies if item.get("name") == "auto-router"]
    assert len(router_deps) == 1
    return router_deps[0]


def test_explicit_ok_true_remains_healthy(monkeypatch):
    dep = _router_dependency(
        monkeypatch, {"ok": True, "service": "auto-router"}
    )
    assert dep["status"] == "healthy"
    assert dep["detail"] == "strict-offline router is reachable"
    assert dep["latency_ms"] == 1.25
    assert dep["metadata"]["service"] == "auto-router"


def test_explicit_ok_false_is_unhealthy(monkeypatch):
    dep = _router_dependency(monkeypatch, {"ok": False, "service": "auto-router"})
    assert dep["status"] == "unhealthy"
    assert dep["detail"] == "router health reported failure"


def test_missing_ok_verdict_is_unhealthy(monkeypatch):
    # A router/proxy that answers with a JSON body but no explicit verdict
    # must not be treated as reachable (old code defaulted to True).
    dep = _router_dependency(
        monkeypatch, {"service": "auto-router", "status": "degraded"}
    )
    assert dep["status"] == "unhealthy"
    assert "ok" in dep["detail"].lower()
    assert dep["detail"] != "strict-offline router is reachable"


def test_empty_health_object_is_unhealthy(monkeypatch):
    dep = _router_dependency(monkeypatch, {})
    assert dep["status"] == "unhealthy"
    assert dep["detail"] != "strict-offline router is reachable"


def test_non_object_health_payload_is_unhealthy_not_an_exception(monkeypatch):
    dep = _router_dependency(monkeypatch, [])
    assert dep["status"] == "unhealthy"
    assert dep["detail"] != "strict-offline router is reachable"


@pytest.mark.parametrize("verdict", ["false", "0", 1])
def test_non_boolean_ok_verdict_is_unhealthy(monkeypatch, verdict):
    dep = _router_dependency(monkeypatch, {"ok": verdict})
    assert dep["status"] == "unhealthy"
    assert dep["detail"] != "strict-offline router is reachable"
