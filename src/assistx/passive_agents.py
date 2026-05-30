from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field


class AgentHeartbeatPlanIn(BaseModel):
    agent_id: str
    status: str = Field(default="idle", description="idle|busy|paused|interrupted|offline")
    capabilities: list[str] = Field(default_factory=list)
    current_task_id: Optional[str] = None
    current_focus: Optional[str] = None
    max_suggestions: int = 3
    mode: str = Field(default="passive", description="passive|review_only|claim_ready")
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_passive_agent_router(neo_factory: Callable[[], Any], auth_dependency: Any | None = None) -> APIRouter:
    """Agent idle-work heartbeat planner.

    This router gives agents a safe passive loop:
    - report heartbeat/status;
    - receive next suggested background work;
    - do not auto-claim tasks;
    - do not dispatch work;
    - keep user-interactive work ahead of idle backlog work.
    """

    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []
    router = APIRouter(prefix="/api/agents", tags=["passive-agents"], dependencies=dependencies)

    @router.post("/heartbeat-plan")
    def heartbeat_plan(body: AgentHeartbeatPlanIn) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        normalized_status = _normalize_status(body.status)
        current = _safe_current_task(body.current_task_id)
        candidates = _suggest_idle_work(
            neo_factory,
            capabilities=body.capabilities,
            limit=max(1, min(int(body.max_suggestions or 3), 10)),
            current_task_id=body.current_task_id,
        )
        plan = _plan_for_status(
            status=normalized_status,
            current_task=current,
            candidates=candidates,
            mode=body.mode,
        )
        _record_agent_heartbeat(
            neo_factory,
            agent_id=body.agent_id,
            status=normalized_status,
            capabilities=body.capabilities,
            current_task_id=body.current_task_id,
            current_focus=body.current_focus,
            mode=body.mode,
            plan=plan,
            metadata=body.metadata,
            now_ms=now_ms,
        )
        return {
            "agent_id": body.agent_id,
            "status": normalized_status,
            "received_at_ts": now_ms,
            "mode": body.mode,
            "current_task_id": body.current_task_id,
            "plan": plan,
            "suggestions": candidates,
            "read_only": True,
            "mutations": ["AgentHeartbeat upsert only"],
            "notes": "This endpoint recommends passive work but does not claim, execute, dispatch, or mutate tasks.",
        }

    @router.get("/idle-work")
    def idle_work(
        capabilities: Optional[list[str]] = Query(None),
        limit: int = Query(5, ge=1, le=25),
    ) -> dict[str, Any]:
        candidates = _suggest_idle_work(neo_factory, capabilities=capabilities or [], limit=limit)
        return {
            "items": candidates,
            "count": len(candidates),
            "read_only": True,
            "notes": "Candidate idle work only; callers must use an approved claim/dispatch path before execution.",
        }

    return router


def _normalize_status(status: str) -> str:
    value = (status or "idle").strip().lower()
    if value in {"idle", "busy", "paused", "interrupted", "offline"}:
        return value
    return "idle"


def _safe_current_task(task_id: Optional[str]) -> dict[str, Any] | None:
    if not task_id:
        return None
    return {"task_id": task_id, "resume_policy": "preserve_and_resume_when_idle"}


def _plan_for_status(
    status: str,
    current_task: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    mode: str,
) -> dict[str, Any]:
    if status == "offline":
        return {"action": "standby", "reason": "agent reported offline"}
    if status == "interrupted":
        return {
            "action": "pause_current_and_wait_for_user_work",
            "reason": "agent reported interruption; preserve current task and prioritize new interactive work",
            "resume": current_task,
        }
    if status == "paused":
        return {"action": "stay_paused", "reason": "agent is paused by operator or local policy", "resume": current_task}
    if status == "busy":
        return {
            "action": "continue_current",
            "reason": "agent is already busy; do not pull passive backlog work",
            "resume": current_task,
        }
    if not candidates:
        return {"action": "idle_wait", "reason": "no eligible passive work found"}
    if mode == "claim_ready":
        return {
            "action": "recommend_claim_via_approved_endpoint",
            "reason": "eligible passive work found; caller may claim only through approved task-claim API",
            "recommended_task_id": candidates[0].get("task_id"),
        }
    return {
        "action": "review_next_candidate",
        "reason": "eligible passive work found; review-only/dry-run recommendation",
        "recommended_task_id": candidates[0].get("task_id"),
    }


def _suggest_idle_work(
    neo_factory: Callable[[], Any],
    capabilities: list[str],
    limit: int,
    current_task_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            rows = s.run(
                """
                MATCH (t:Task)
                WHERE t.status IN ['READY','REVIEW']
                  AND ($current_task_id IS NULL OR t.id <> $current_task_id)
                RETURN t
                ORDER BY
                  CASE coalesce(t.priority, '')
                    WHEN 'HIGH' THEN 0
                    WHEN 'MEDIUM' THEN 1
                    WHEN 'LOW' THEN 2
                    ELSE 3
                  END,
                  coalesce(t.created_at_ts, t.updated_at_ts, 0) ASC
                LIMIT $limit
                """,
                {"current_task_id": current_task_id, "limit": int(limit) * 3},
            )
            out: list[dict[str, Any]] = []
            for row in rows:
                task = dict(row["t"])
                item = _normalize_idle_candidate(task)
                if _is_passive_safe(item) and _capabilities_match(item, capabilities):
                    out.append(item)
                if len(out) >= limit:
                    break
            return out
    except Exception:
        return []
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def _normalize_idle_candidate(task: dict[str, Any]) -> dict[str, Any]:
    payload = _json_dict(task.get("payload_json") or task.get("payload"))
    metadata = _json_dict(task.get("metadata_json") or task.get("metadata"))
    required = task.get("required_capabilities") or payload.get("required_capabilities") or []
    if isinstance(required, str):
        required = [part.strip() for part in required.split(",") if part.strip()]
    privacy = str(task.get("privacy") or task.get("privacy_label") or payload.get("privacy") or metadata.get("privacy") or "").lower()
    queue_class = str(payload.get("queue_class") or task.get("queue_class") or metadata.get("queue_class") or "background")
    title = str(task.get("title") or payload.get("title") or task.get("id") or "AssistX task")
    return {
        "task_id": str(task.get("id") or task.get("task_id") or title),
        "title": title,
        "status": str(task.get("status") or "UNKNOWN"),
        "kind": task.get("kind"),
        "priority": task.get("priority") or payload.get("priority"),
        "queue_class": queue_class,
        "required_capabilities": required if isinstance(required, list) else [],
        "privacy": privacy,
        "local_only": bool(task.get("local_only") or payload.get("local_only") or metadata.get("local_only") or privacy in {"local_only", "private", "secret"}),
        "sensitive": bool(task.get("sensitive") or payload.get("sensitive") or metadata.get("sensitive") or privacy in {"private", "secret", "voice_auth", "enrollment", "enrollment_sample"}),
        "created_at_ts": task.get("created_at_ts"),
        "updated_at_ts": task.get("updated_at_ts"),
    }


def _is_passive_safe(item: dict[str, Any]) -> bool:
    if item.get("sensitive") or item.get("local_only"):
        return False
    queue_class = str(item.get("queue_class") or "background").lower()
    if queue_class in {"critical", "interactive"}:
        return False
    return str(item.get("status") or "").upper() in {"READY", "REVIEW"}


def _capabilities_match(item: dict[str, Any], capabilities: list[str]) -> bool:
    required = set(str(x).lower() for x in item.get("required_capabilities") or [])
    if not required:
        return True
    available = set(str(x).lower() for x in capabilities or [])
    return required.issubset(available)


def _record_agent_heartbeat(
    neo_factory: Callable[[], Any],
    agent_id: str,
    status: str,
    capabilities: list[str],
    current_task_id: Optional[str],
    current_focus: Optional[str],
    mode: str,
    plan: dict[str, Any],
    metadata: dict[str, Any],
    now_ms: int,
) -> None:
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            s.run(
                """
                MERGE (a:AgentHeartbeat {agent_id:$agent_id})
                SET a.status=$status,
                    a.capabilities=$capabilities,
                    a.current_task_id=$current_task_id,
                    a.current_focus=$current_focus,
                    a.mode=$mode,
                    a.plan_json=$plan_json,
                    a.metadata_json=$metadata_json,
                    a.last_seen_at=datetime(),
                    a.last_seen_at_ts=$now_ms
                """,
                {
                    "agent_id": agent_id,
                    "status": status,
                    "capabilities": capabilities,
                    "current_task_id": current_task_id,
                    "current_focus": current_focus,
                    "mode": mode,
                    "plan_json": json.dumps(plan, sort_keys=True),
                    "metadata_json": json.dumps(metadata or {}, sort_keys=True),
                    "now_ms": int(now_ms),
                },
            ).consume()
    except Exception:
        return
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}
