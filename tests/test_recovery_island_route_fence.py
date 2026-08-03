from __future__ import annotations

from assistx.api_router import app


def test_recovery_execute_route_is_replaced_once():
    path = "/api/fleet/recovery-control/proposals/{proposal_id}/execute"
    routes = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == path
        and "POST" in (getattr(route, "methods", None) or set())
    ]

    assert len(routes) == 1
    assert routes[0].endpoint.__module__ == "assistx.recovery_island_routes"
    assert routes[0].endpoint.__name__ == "execute_recovery_proposal"
