from __future__ import annotations

from typing import Any, Callable

from . import api as api_module
from . import control_room as control_room_module
from . import router_integration as router_integration_module
from .api import _neo, app, auth, templates
from .benchmark_allocation_policy import install_benchmark_allocation_policy
from .control_room import LEGACY_UI_PATHS, build_control_room_router
from .control_room_runtime import install_control_room_runtime
from .degraded_activation import (
    build_degraded_activation_router,
    install_degraded_activation_fence,
)
from .degraded_control_hardening import install_degraded_control_hardening
from .degraded_control_plane import (
    build_default_runtime,
    build_degraded_control_router,
    install_degraded_route_fence,
)
from .degraded_router_gate import (
    install_degraded_router_activation_requirements,
)
from .executor_security import install_executor_security
from .fleet_context_projection import install_fleet_node_context_projection
from .fleet_routing_matrix import build_fleet_routing_matrix_router
from .overlay_routes import build_overlay_router
from .passive_agents import build_passive_agent_router
from .passive_claims import build_passive_claim_router
from .passive_control import build_passive_control_router
from .coordination_routes import build_coordination_router
from .voice_identity_routes import build_voice_identity_router
from .doctor_routes import build_doctor_router
from .passive_events import build_passive_event_router
from .passive_status import build_passive_status_router
from .recovery_island_routes import build_recovery_island_router
from .recovery_mode import build_recovery_mode_router, install_recovery_shadow_mode
from .router_integration import build_router_integration_router
from .runtime_projection_v2 import build_runtime_projection_router_v2
from .routers.devices import build_devices_router
from .routers.dispatch import build_dispatch_router
from .routers.feeds import build_feeds_router
from .routers.intents import build_intents_router
from .routers.memory import build_memory_router
from .routers.review import build_review_router
from .routers.tickets import build_tickets_router
from .routers.transcriptions import build_transcriptions_router
from .strict_offline_projection import install_strict_offline_projection

_LEGACY_RECOVERY_EXECUTE_PATH = (
    "/api/fleet/recovery-control/proposals/{proposal_id}/execute"
)


def _remove_superseded_operator_routes() -> None:
    """Keep legacy APIs but replace overlapping dashboard pages with one control room."""

    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) in LEGACY_UI_PATHS
            and "GET" in (getattr(route, "methods", None) or set())
        )
    ]


def _extract_legacy_recovery_execute() -> Callable[..., Any] | None:
    """Replace one overlapping route while preserving non-island behavior."""

    endpoint: Callable[..., Any] | None = None
    retained = []
    for route in app.router.routes:
        is_target = (
            getattr(route, "path", None) == _LEGACY_RECOVERY_EXECUTE_PATH
            and "POST" in (getattr(route, "methods", None) or set())
        )
        if is_target and endpoint is None:
            endpoint = getattr(route, "endpoint", None)
            continue
        retained.append(route)
    app.router.routes = retained
    return endpoint


# Must run during module import, before the ASGI server enters the app lifespan.
# Recovery-shadow mode disables normal mutation loops. Degraded mode then adds a
# narrow HTTP fence; a second fence keeps coordination writes and provider
# projections locked until the Beelink verifies a signed activation envelope.
install_recovery_shadow_mode(api_module)
install_control_room_runtime(control_room_module)
install_strict_offline_projection(router_integration_module)
install_fleet_node_context_projection(router_integration_module, _neo)
install_benchmark_allocation_policy(api_module, _neo)
install_executor_security(app, _neo, api_module)
install_degraded_control_hardening()
install_degraded_router_activation_requirements()
install_degraded_route_fence(app)
install_degraded_activation_fence(
    app,
    lambda: build_default_runtime(_neo),
)
_remove_superseded_operator_routes()
_legacy_recovery_execute = _extract_legacy_recovery_execute()
app.include_router(build_degraded_control_router(auth, neo_factory=_neo))
app.include_router(build_degraded_activation_router(auth, neo_factory=_neo))
app.include_router(build_recovery_mode_router(auth))
app.include_router(
    build_recovery_island_router(
        _neo,
        auth_dependency=auth,
        legacy_recovery_execute=_legacy_recovery_execute,
    )
)
app.include_router(build_control_room_router(_neo, auth, templates))
app.include_router(build_fleet_routing_matrix_router(_neo, auth))
app.include_router(build_router_integration_router(_neo))
app.include_router(build_runtime_projection_router_v2(_neo, auth_dependency=auth))
app.include_router(build_overlay_router())
app.include_router(build_passive_agent_router(_neo, auth_dependency=auth))
app.include_router(build_passive_claim_router(_neo, auth_dependency=auth))
app.include_router(build_passive_control_router(_neo, auth_dependency=auth))
app.include_router(build_coordination_router(auth_dependency=auth))
app.include_router(build_voice_identity_router(_neo, auth_dependency=auth))
app.include_router(build_doctor_router())
app.include_router(build_passive_status_router(_neo, auth_dependency=auth))
app.include_router(build_passive_event_router(_neo, auth_dependency=auth))
app.include_router(build_devices_router())
app.include_router(build_feeds_router())
app.include_router(build_review_router())
app.include_router(build_intents_router())
app.include_router(build_tickets_router())
app.include_router(build_memory_router())
app.include_router(build_transcriptions_router())
app.include_router(build_dispatch_router())
