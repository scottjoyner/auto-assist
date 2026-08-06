"""Install fail-closed claim identity checks on worker mutation methods."""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)

_INSTALLED = False


def _enabled() -> bool:
    return os.getenv("ASSISTX_REQUIRE_WORKER_CLAIM_ID", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _legacy_agents() -> set[str]:
    return {
        item.strip()
        for item in os.getenv("ASSISTX_LEGACY_CLAIMLESS_AGENTS", "").split(",")
        if item.strip()
    }


def install_strict_claim_fencing() -> None:
    """Require ``claim_id`` for heartbeat and completion mutations.

    A temporary explicit legacy-agent allowlist is available for staged
    migration. New deployments should leave it empty.
    """

    global _INSTALLED
    if _INSTALLED:
        return
    from .neo4j_client import Neo4jClient

    original_heartbeat = Neo4jClient.heartbeat_task
    original_complete = Neo4jClient.complete_task

    @wraps(original_heartbeat)
    def heartbeat_task(
        self: Any,
        task_id: str,
        agent_id: str,
        status: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        lease_seconds: int | None = None,
        claim_id: str | None = None,
    ) -> dict[str, Any] | None:
        if _enabled() and not str(claim_id or "").strip() and agent_id not in _legacy_agents():
            logger.warning(
                "rejected claimless heartbeat task=%s agent=%s", task_id, agent_id
            )
            return None
        return original_heartbeat(
            self,
            task_id,
            agent_id,
            status=status,
            session_id=session_id,
            metadata=metadata,
            lease_seconds=lease_seconds,
            claim_id=claim_id,
        )

    @wraps(original_complete)
    def complete_task(
        self: Any,
        task_id: str,
        agent_id: str,
        status: str,
        summary: str | None = None,
        result: dict[str, Any] | None = None,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        claim_id: str | None = None,
    ) -> dict[str, Any] | None:
        if _enabled() and not str(claim_id or "").strip() and agent_id not in _legacy_agents():
            logger.warning(
                "rejected claimless completion task=%s agent=%s", task_id, agent_id
            )
            return None
        return original_complete(
            self,
            task_id,
            agent_id,
            status,
            summary=summary,
            result=result,
            session_id=session_id,
            idempotency_key=idempotency_key,
            claim_id=claim_id,
        )

    Neo4jClient.heartbeat_task = heartbeat_task
    Neo4jClient.complete_task = complete_task
    _INSTALLED = True
