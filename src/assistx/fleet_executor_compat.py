"""Compatibility exports for fleet dashboard readers.

The old executor exposed a module-global benchmark routing singleton. The safe
executor keeps routing state on the active projection-driven executor instead.
This shim preserves the read-only dashboard import without restoring legacy
node discovery or dispatch behavior.
"""

from __future__ import annotations


def install_fleet_executor_compatibility() -> None:
    from . import fleet_executor

    fallback = fleet_executor.FleetRouting()

    def get_routing():
        running = fleet_executor.get_fleet_executor()
        if running is not None:
            return running._routing
        return fallback

    fleet_executor._get_routing = get_routing
