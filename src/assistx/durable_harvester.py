"""Fleet-wide durable wrapper for the knowledge insight harvester."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable

from .controller_runtime import (
    DurableController,
    Neo4jControllerStore,
    start_durable_controller_loop,
)
from .neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

_start_lock = threading.Lock()
_started = False


def start_durable_harvester_loop() -> None:
    """Run one KG harvester leader across all API processes and hosts."""

    global _started
    enabled = os.getenv("ASSISTX_KG_HARVESTER_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        logger.info("kg harvester: disabled by ASSISTX_KG_HARVESTER_ENABLED")
        return
    with _start_lock:
        if _started:
            return
        _started = True

        # Import lazily so package initialization can install this wrapper
        # without creating a circular import.
        from . import kg_harvester

        interval = max(10, int(float(kg_harvester.HARVEST_INTERVAL)))

        def store_factory() -> tuple[Neo4jControllerStore, Callable[[], None]]:
            neo = Neo4jClient()
            return Neo4jControllerStore(neo), neo.close

        controller = DurableController(
            "kg-insight-harvester",
            store_factory,
            lease_seconds=max(60, interval * 6),
        )

        def harvest() -> dict[str, Any]:
            worker = kg_harvester.KgInsightHarvester()
            try:
                created = int(worker.harvest_until_target() or 0)
                return {"created": created}
            finally:
                worker.close()

        start_durable_controller_loop(
            controller,
            harvest,
            interval_seconds=interval,
        )
        logger.info("kg harvester: durable fleet-wide loop started")
