"""Full AssistX integration smoke: synthetic auth only, no real private inventory or workers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("path", ["/api/fleet/hardware-preview", "/fleet/hardware-evidence"])
def test_full_app_authentication_fences_hardware_routes(path: str) -> None:
    pytest.importorskip("langgraph")
    from assistx.api_router import app, auth

    route = [r for r in app.routes if getattr(r, "path", None) == path]
    assert len(route) == 1
    assert route[0].methods == {"GET"}
    assert any(item.call is auth for item in route[0].dependant.dependencies)
    assert TestClient(app).get(path).status_code in (401, 403)


def test_full_app_standalone_operator_page_renders_without_private_checkout(monkeypatch) -> None:
    pytest.importorskip("langgraph")
    from assistx.api_router import app, auth

    monkeypatch.delenv("ASSISTX_FLEET_HARDWARE_ROOT", raising=False)
    client = TestClient(app)
    app.dependency_overrides[auth] = lambda: "synthetic-test-operator"
    try:
        page = client.get("/fleet/hardware-evidence")
        api = client.get("/api/fleet/hardware-preview")
        control = client.get("/control-room")
    finally:
        app.dependency_overrides.pop(auth, None)

    assert page.status_code == 200
    assert 'id="fleet-hardware-evidence-form"' in page.text
    assert "/static/js/fleet_hardware_preview.js" in page.text
    assert page.headers["Cache-Control"] == "private, no-store"
    assert api.status_code == 503  # No private checkout configured in public CI.
    assert control.status_code == 200
    assert "/fleet/hardware-evidence" in control.text


def test_inspection_receipt_route_auth_and_disabled_by_default(monkeypatch) -> None:
    pytest.importorskip("langgraph")
    from assistx.api_router import app, auth

    monkeypatch.delenv("ASSISTX_FLEET_HARDWARE_INSPECTION_WRITES", raising=False)
    path = "/api/fleet/trace-inspections/trace-123"
    routes = [r for r in app.routes if getattr(r, "path", None) == "/api/fleet/trace-inspections/{correlation_id}"]
    assert len(routes) == 2
    assert {tuple(sorted(r.methods)) for r in routes} == {("GET",), ("POST",)}
    client = TestClient(app)
    assert client.get(path).status_code in (401, 403)
    assert client.post(path, json={}).status_code in (401, 403)
    app.dependency_overrides[auth] = lambda: "synthetic-operator"
    try:
        response = client.post(
            path,
            json={"ram_gib": 20.0, "confirm_post_hoc_observation": True},
            headers={"X-AssistX-Operator-Intent": "fleet-hardware-inspection-v1"},
        )
    finally:
        app.dependency_overrides.pop(auth, None)
    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]
