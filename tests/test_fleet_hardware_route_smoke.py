"""Full application import/auth registration smoke for fleet hardware preview.

Requires the complete existing AssistX runtime dependencies. No private fleet
inventory and no services are started; assertions only validate route wiring.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_full_app_registers_authenticated_readonly_fleet_hardware_route():
    from assistx.api_router import app, auth

    route = [r for r in app.routes if getattr(r, "path", None) == "/api/fleet/hardware-preview"]
    assert len(route) == 1
    assert route[0].methods == {"GET"}
    assert any(entry.call is auth for entry in route[0].dependant.dependencies)

    response = TestClient(app).get("/api/fleet/hardware-preview")
    assert response.status_code in (401, 403), (
        "Unauthenticated fleet hardware access must be rejected before source files are read"
    )


def test_full_dashboard_contains_opt_in_hardware_section():
    from assistx.api_router import app, auth

    app.dependency_overrides[auth] = lambda: "synthetic-operator"
    try:
        response = TestClient(app).get("/fleet-dashboard")
    finally:
        app.dependency_overrides.pop(auth, None)
    assert response.status_code == 200
    assert 'id="section-hardware-evidence"' in response.text
    assert 'id="hardware-evidence-form"' in response.text
    assert "fleet_hardware_preview.js" in response.text
    assert "read-only / no dispatch" in response.text
