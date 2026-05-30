from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from assistx.passive_agents import _capabilities_match, _is_passive_safe, _normalize_idle_candidate


class PassiveClaimIn(BaseModel):
    agent_id: str
    task_id: str
    lease_id: Optional[str] = None
    capabilities: list[str] = Field(default_factory=list)
    mode: str = Field(default="review_only", description="review_only|claim_ready")
    ttl_seconds: int = Field(default=1800, ge=60, le=7200)
    operator_approved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PassiveClaimReleaseIn(BaseModel):
    agent_id: str
    claim_id: str
    task_id: str
    result: str = Field(default="released", description="released|completed_review|abandoned|interrupted")
    summary: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_passive_claim_router(neo_factory: Callable[[], Any], auth_dependency: Any | None = None) -> APIRouter:
    """Review-only passive work claim endpoints.

    A passive claim reserves a safe task so an idle agent can review/plan it
    without executing code or mutating external systems. It is a lightweight
    coordination primitive between heartbeat recommendations and future real
    execution claims.
    """

    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []
    router = APIRouter(prefix="/api/agents", tags=["passive-claims"], dependencies=dependencies)

    @router.post("/passive-claim")
    def passive_claim(body: PassiveClaimIn) -> dict[str, Any]:
        result = claim_passive_task(neo_factory, body)
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result)
        return result

    @router.post("/passive-claim/release")
    def passive_claim_release(body: PassiveClaimReleaseIn) -> dict[str, Any]:
        result = release_passive_claim(neo_factory, body)
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result)
        return result

    return router


def claim_passive_task(neo_factory: Callable[[], Any], body: PassiveClaimIn) -> dict[str, Any]:
    if body.mode not in {"review_only", "claim_ready", "passive"}:
        return _blocked("invalid_mode", f"mode {body.mode!r} is not allowed for passive claims")
    if body.mode == "claim_ready" and not body.operator_approved:
        return _blocked("approval_required", "claim_ready mode requires operator_approved=true")

    now_ms = int(time.time() * 1000)
    claim_id = str(uuid.uuid4())
    expires_at_ts = now_ms + (int(body.ttl_seconds) * 1000)
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            row = s.execute_write(_tx_claim_passive_task, body, claim_id, now_ms, expires_at_ts)
    except Exception as exc:
        return _blocked("neo4j_error", str(exc)[:500])
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass

    if not row:
        return _blocked("not_claimed", "task was not claimable; it may be missing, already claimed, or not in READY/REVIEW")
    task = dict(row.get("task") or {})
    candidate = _normalize_idle_candidate(task, now_ms=now_ms)
    if not _is_passive_safe(candidate):
        _rollback_claim(neo_factory, body.task_id, claim_id, reason="safety_rejected")
        return _blocked("safety_rejected", "task is not passive-safe", {"candidate": candidate})
    if not _capabilities_match(candidate, body.capabilities):
        _rollback_claim(neo_factory, body.task_id, claim_id, reason="capability_mismatch")
        return _blocked("capability_mismatch", "agent capabilities do not satisfy task requirements", {"candidate": candidate})

    return {
        "ok": True,
        "claim_id": claim_id,
        "task_id": body.task_id,
        "agent_id": body.agent_id,
        "mode": body.mode,
        "status": "CLAIMED_PASSIVE",
        "lease_id": body.lease_id,
        "issued_at_ts": now_ms,
        "expires_at_ts": expires_at_ts,
        "ttl_seconds": int(body.ttl_seconds),
        "review_only": True,
        "execution_allowed": False,
        "write_allowed": False,
        "candidate": candidate,
        "contract": {
            "task_claim": "passive_review_only",
            "execution": "not_performed",
            "dispatch": "not_performed",
            "repo_write": "not_allowed",
            "operator_approval_required_for_execution": True,
        },
    }


def release_passive_claim(neo_factory: Callable[[], Any], body: PassiveClaimReleaseIn) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            row = s.execute_write(_tx_release_passive_claim, body, now_ms)
    except Exception as exc:
        return _blocked("neo4j_error", str(exc)[:500])
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass
    if not row:
        return _blocked("not_released", "passive claim was not found or is owned by another agent")
    return {
        "ok": True,
        "claim_id": body.claim_id,
        "task_id": body.task_id,
        "agent_id": body.agent_id,
        "result": body.result,
        "released_at_ts": now_ms,
        "next_status": row.get("next_status"),
        "summary": body.summary,
    }


def _tx_claim_passive_task(tx: Any, body: PassiveClaimIn, claim_id: str, now_ms: int, expires_at_ts: int) -> dict[str, Any] | None:
    result = tx.run(
        """
        MATCH (t:Task)
        WHERE coalesce(t.id, t.task_id) = $task_id
          AND t.status IN ['READY','REVIEW']
        SET t.previous_status = t.status,
            t.status = 'CLAIMED_PASSIVE',
            t.passive_claim_id = $claim_id,
            t.passive_claim_agent_id = $agent_id,
            t.passive_claim_lease_id = $lease_id,
            t.passive_claim_mode = $mode,
            t.passive_claimed_at = datetime(),
            t.passive_claimed_at_ts = $now_ms,
            t.passive_claim_expires_at_ts = $expires_at_ts,
            t.passive_claim_metadata_json = $metadata_json
        MERGE (a:AgentHeartbeat {agent_id:$agent_id})
        SET a.status = 'busy',
            a.current_task_id = coalesce(t.id, t.task_id),
            a.mode = $mode,
            a.last_seen_at = datetime(),
            a.last_seen_at_ts = $now_ms,
            a.action = 'passive_claimed',
            a.recommended_task_id = coalesce(t.id, t.task_id)
        MERGE (a)-[:PASSIVELY_CLAIMED]->(t)
        RETURN t AS task
        """,
        {
            "task_id": body.task_id,
            "claim_id": claim_id,
            "agent_id": body.agent_id,
            "lease_id": body.lease_id,
            "mode": body.mode,
            "now_ms": int(now_ms),
            "expires_at_ts": int(expires_at_ts),
            "metadata_json": json.dumps(body.metadata or {}, sort_keys=True),
        },
    )
    row = result.single()
    return dict(row) if row else None


def _tx_release_passive_claim(tx: Any, body: PassiveClaimReleaseIn, now_ms: int) -> dict[str, Any] | None:
    next_status = _next_status_for_release(body.result)
    result = tx.run(
        """
        MATCH (t:Task)
        WHERE coalesce(t.id, t.task_id) = $task_id
          AND t.passive_claim_id = $claim_id
          AND t.passive_claim_agent_id = $agent_id
        SET t.status = $next_status,
            t.last_passive_claim_id = t.passive_claim_id,
            t.last_passive_claim_agent_id = t.passive_claim_agent_id,
            t.last_passive_claim_result = $result,
            t.last_passive_claim_summary = $summary,
            t.last_passive_claim_released_at = datetime(),
            t.last_passive_claim_released_at_ts = $now_ms,
            t.last_passive_claim_metadata_json = $metadata_json
        REMOVE t.passive_claim_id,
               t.passive_claim_agent_id,
               t.passive_claim_lease_id,
               t.passive_claim_mode,
               t.passive_claimed_at,
               t.passive_claimed_at_ts,
               t.passive_claim_expires_at_ts,
               t.passive_claim_metadata_json
        MERGE (a:AgentHeartbeat {agent_id:$agent_id})
        SET a.status = 'idle',
            a.current_task_id = null,
            a.last_completed_task_id = CASE WHEN $result = 'completed_review' THEN coalesce(t.id, t.task_id) ELSE a.last_completed_task_id END,
            a.last_result_summary = $summary,
            a.last_seen_at = datetime(),
            a.last_seen_at_ts = $now_ms,
            a.action = 'passive_claim_released',
            a.recommended_task_id = null
        RETURN $next_status AS next_status
        """,
        {
            "task_id": body.task_id,
            "claim_id": body.claim_id,
            "agent_id": body.agent_id,
            "result": body.result,
            "summary": (body.summary or "")[:1000] if body.summary else None,
            "next_status": next_status,
            "now_ms": int(now_ms),
            "metadata_json": json.dumps(body.metadata or {}, sort_keys=True),
        },
    )
    row = result.single()
    return dict(row) if row else None


def _rollback_claim(neo_factory: Callable[[], Any], task_id: str, claim_id: str, reason: str) -> None:
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            s.run(
                """
                MATCH (t:Task)
                WHERE coalesce(t.id, t.task_id) = $task_id
                  AND t.passive_claim_id = $claim_id
                SET t.status = coalesce(t.previous_status, 'REVIEW'),
                    t.last_passive_claim_result = $reason,
                    t.last_passive_claim_released_at = datetime()
                REMOVE t.passive_claim_id,
                       t.passive_claim_agent_id,
                       t.passive_claim_lease_id,
                       t.passive_claim_mode,
                       t.passive_claimed_at,
                       t.passive_claimed_at_ts,
                       t.passive_claim_expires_at_ts,
                       t.passive_claim_metadata_json
                """,
                {"task_id": task_id, "claim_id": claim_id, "reason": reason},
            ).consume()
    except Exception:
        return
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def _next_status_for_release(result: str) -> str:
    normalized = (result or "released").lower()
    if normalized == "completed_review":
        return "REVIEW"
    if normalized in {"interrupted", "abandoned", "released"}:
        return "READY"
    return "READY"


def _blocked(reason: str, message: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "message": message, **(extra or {})}
