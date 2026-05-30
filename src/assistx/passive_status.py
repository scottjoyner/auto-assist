from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Query

from assistx.passive_agents import _heartbeat_counts, _read_agent_heartbeats, _suggest_idle_work
from assistx.passive_claims import expire_passive_claims, list_passive_claims, passive_claim_summary


def build_passive_status_router(neo_factory: Callable[[], Any], auth_dependency: Any | None = None) -> APIRouter:
    """Combined passive-agent coordination status endpoints."""

    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []
    router = APIRouter(prefix="/api/agents", tags=["passive-status"], dependencies=dependencies)

    @router.get("/passive-status")
    def passive_status(
        agent_id: Optional[str] = None,
        include_idle_work: bool = True,
        limit: int = Query(25, ge=1, le=100),
    ) -> dict[str, Any]:
        return build_passive_status(
            neo_factory,
            agent_id=agent_id,
            include_idle_work=include_idle_work,
            limit=limit,
        )

    @router.post("/passive-maintenance")
    def passive_maintenance(limit: int = Query(50, ge=1, le=250)) -> dict[str, Any]:
        expired = expire_passive_claims(neo_factory, limit=limit)
        status = build_passive_status(neo_factory, include_idle_work=False, limit=min(limit, 100))
        return {
            "ok": bool(expired.get("ok")),
            "expired": expired,
            "status": status,
            "next_action": "agents should heartbeat after maintenance before starting new passive work",
        }

    return router


def build_passive_status(
    neo_factory: Callable[[], Any],
    agent_id: Optional[str] = None,
    include_idle_work: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    heartbeats = _read_agent_heartbeats(neo_factory, limit=limit)
    claims = list_passive_claims(neo_factory, agent_id=agent_id, include_expired=True, limit=limit)
    idle_work = _suggest_idle_work(neo_factory, capabilities=[], limit=min(limit, 10)) if include_idle_work else []
    if agent_id:
        heartbeats = [item for item in heartbeats if item.get("agent_id") == agent_id]
    heartbeat_summary = _heartbeat_counts(heartbeats)
    claim_summary = passive_claim_summary(claims)
    recommendations = passive_system_recommendations(heartbeat_summary, claim_summary, idle_work)
    return {
        "ok": True,
        "agent_id": agent_id,
        "heartbeats": heartbeats,
        "heartbeat_summary": heartbeat_summary,
        "claims": claims,
        "claim_summary": claim_summary,
        "idle_work": idle_work,
        "idle_work_count": len(idle_work),
        "recommendations": recommendations,
        "read_only": True,
    }


def passive_system_recommendations(
    heartbeat_summary: dict[str, int],
    claim_summary: dict[str, int],
    idle_work: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    if claim_summary.get("expired", 0) > 0:
        recs.append(
            {
                "level": "warning",
                "action": "expire_stale_claims",
                "reason": "expired passive claims are present and may block idle work",
            }
        )
    if heartbeat_summary.get("idle", 0) > 0 and idle_work:
        recs.append(
            {
                "level": "info",
                "action": "heartbeat_idle_agents",
                "reason": "idle agents and passive-safe work are available",
            }
        )
    if heartbeat_summary.get("busy", 0) > 0:
        recs.append(
            {
                "level": "info",
                "action": "monitor_claim_renewals",
                "reason": "busy agents should renew or release passive claims before TTL expiry",
            }
        )
    if not idle_work and claim_summary.get("active", 0) == 0:
        recs.append(
            {
                "level": "info",
                "action": "idle_wait",
                "reason": "no active passive claims and no passive-safe idle work found",
            }
        )
    return recs
