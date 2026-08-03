from __future__ import annotations

from .degraded_activation import _ACTIVE_REQUIRED


def install_degraded_router_activation_requirements() -> None:
    """Require an active takeover fence before auto-router sees providers."""

    _ACTIVE_REQUIRED.update(
        {
            ("GET", "/api/degraded/runtime-projection"),
            ("GET", "/api/degraded/context-projection"),
        }
    )
