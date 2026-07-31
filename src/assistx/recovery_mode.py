from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends

logger = logging.getLogger(__name__)

_SHADOW_STATUS: dict[str, Any] = {
    "enabled": False,
    "mode": "normal",
    "disabled_startup_loops": [],
}

_DISABLED_STARTUP_LOOPS = [
    "paperclip_poller",
    "intent_orchestrator",
    "maintenance_scheduler",
    "stale_claim_reaper",
    "model_endpoint_prober",
    "fleet_executor",
    "recovery_reconciler",
    "execution_reconciler",
    "knowledge_graph_harvester",
    "repository_task_generator",
    "fleet_model_loader",
]


def recovery_shadow_enabled() -> bool:
    return os.getenv("ASSISTX_RECOVERY_SHADOW_MODE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def install_recovery_shadow_mode(api_module: Any) -> dict[str, Any]:
    """Replace the normal API lifespan with an inert recovery lifespan.

    A restored Neo4j database can contain READY tasks, stale leases, intents,
    dispatches, and model state. The normal API lifespan starts several
    background loops that mutate or execute that state. Recovery shadow mode
    starts only the API and schema check; execution is promoted separately.
    """

    global _SHADOW_STATUS
    if not recovery_shadow_enabled():
        _SHADOW_STATUS = {
            "enabled": False,
            "mode": "normal",
            "disabled_startup_loops": [],
        }
        return dict(_SHADOW_STATUS)

    os.environ["LLM_LOADER_DISABLE"] = "1"
    os.environ["EXECUTION_BACKEND"] = "disabled"
    os.environ["ASSISTX_RECOVERY_ISLAND_DISPATCH_ENABLED"] = "false"
    os.environ["ASSISTX_RECOVERY_EXECUTION_ENABLED"] = "false"
    os.environ["ASSISTX_REPO_TASK_GENERATOR_ENABLED"] = "false"

    @asynccontextmanager
    async def recovery_shadow_lifespan(_app):
        api_module.validate_runtime_configuration(strict=True)
        neo = None
        try:
            neo = api_module.Neo4jClient()
            neo.ensure_schema()
        except Exception as exc:  # schema check must not hide the recovery API
            logger.warning("recovery shadow schema check failed: %s", exc)
        finally:
            if neo is not None:
                try:
                    neo.close()
                except Exception:
                    pass
        logger.warning(
            "AssistX recovery shadow mode active; task execution and mutation "
            "controllers are disabled"
        )
        yield

    api_module.app.router.lifespan_context = recovery_shadow_lifespan
    _SHADOW_STATUS = {
        "enabled": True,
        "mode": "recovery_shadow",
        "execution_promoted": False,
        "disabled_startup_loops": list(_DISABLED_STARTUP_LOOPS),
        "promotion_requirements": [
            "exclusive recovery activation fence",
            "healthy isolated Neo4j restore",
            "healthy AssistX API and auto-router",
            "runtime projection convergence",
            "synthetic fenced task",
            "explicit executor deployment activation",
        ],
    }
    return dict(_SHADOW_STATUS)


def recovery_shadow_status() -> dict[str, Any]:
    return dict(_SHADOW_STATUS)


def build_recovery_mode_router(auth_dependency: Any) -> APIRouter:
    router = APIRouter(tags=["recovery-island"])

    @router.get("/api/fleet/recovery-island/shadow-status")
    def shadow_status(_user: str = Depends(auth_dependency)):
        return recovery_shadow_status()

    return router
