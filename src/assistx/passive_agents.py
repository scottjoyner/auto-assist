from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field


class AgentHeartbeatPlanIn(BaseModel):
    agent_id: str
    status: str = Field(default="idle", description="idle|busy|paused|interrupted|offline|draining")
    capabilities: list[str] = Field(default_factory=list)
    current_task_id: Optional[str] = None
    current_focus: Optional[str] = None
    last_completed_task_id: Optional[str] = None
    last_result_summary: Optional[str] = None
    max_suggestions: int = 3
    mode: str = Field(default="passive", description="passive|review_only|claim_ready")
    user_active: bool = Field(default=False, description="True when user-interactive work should preempt passive work")
    allow_resume_current: bool = True
    max_work_seconds: int = Field(default=900, ge=60, le=7200)
    interrupt_policy: str = Field(default="pause_and_resume", description="pause_and_resume|finish_current_step|stop_now")
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_passive_agent_router(neo_factory: Callable[[], Any], auth_dependency: Any | None = None) -> APIRouter:
    """Agent idle-work heartbeat planner.

    This router gives agents a safe passive loop:
    - report heartbeat/status;
    - receive next suggested background work;
    - preserve/resume current passive focus;
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
        candidates = _suggest_idle_work(
            neo_factory,
            capabilities=body.capabilities,
            limit=max(1, min(int(body.max_suggestions or 3), 10)),
            current_task_id=body.current_task_id,
            now_ms=now_ms,
        )
        plan = _plan_for_status(
            status=normalized_status,
            current_task=_safe_current_task(body.current_task_id, body.current_focus),
            candidates=candidates,
            mode=body.mode,
            user_active=body.user_active,
            allow_resume_current=body.allow_resume_current,
            max_work_seconds=body.max_work_seconds,
            interrupt_policy=body.interrupt_policy,
            now_ms=now_ms,
        )
        _record_agent_heartbeat(
            neo_factory,
            agent_id=body.agent_id,
            status=normalized_status,
            capabilities=body.capabilities,
            current_task_id=body.current_task_id,
            current_focus=body.current_focus,
            last_completed_task_id=body.last_completed_task_id,
            last_result_summary=body.last_result_summary,
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
            "current_focus": body.current_focus,
            "user_active": body.user_active,
            "plan": plan,
            "suggestions": candidates,
            "read_only": True,
            "mutations": ["AgentHeartbeat upsert only"],
            "contract": {
                "claiming": "not_performed",
                "execution": "not_performed",
                "task_mutation": "not_performed",
                "lease_type": "advisory_only",
                "operator_approval_required_for_write": True,
            },
            "notes": "This endpoint recommends passive work but does not claim, execute, dispatch, or mutate tasks.",
        }

    @router.get("/idle-work")
    def idle_work(
        capabilities: Optional[list[str]] = Query(None),
        limit: int = Query(5, ge=1, le=25),
    ) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        candidates = _suggest_idle_work(neo_factory, capabilities=capabilities or [], limit=limit, now_ms=now_ms)
        return {
            "items": candidates,
            "count": len(candidates),
            "read_only": True,
            "notes": "Candidate idle work only; callers must use an approved claim/dispatch path before execution.",
        }

    @router.get("/heartbeat-summary")
    def heartbeat_summary(limit: int = Query(25, ge=1, le=100)) -> dict[str, Any]:
        items = _read_agent_heartbeats(neo_factory, limit=limit)
        return {
            "items": items,
            "count": len(items),
            "read_only": True,
            "summary": _heartbeat_counts(items),
        }

    return router


def _normalize_status(status: str) -> str:
    value = (status or "idle").strip().lower()
    if value in {"idle", "busy", "paused", "interrupted", "offline", "draining"}:
        return value
    return "idle"


def _safe_current_task(task_id: Optional[str], focus: Optional[str] = None) -> dict[str, Any] | None:
    if not task_id:
        return None
    return {
        "task_id": task_id,
        "focus": focus,
        "resume_policy": "preserve_and_resume_when_idle",
    }


def _plan_for_status(
    status: str,
    current_task: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    mode: str,
    user_active: bool = False,
    allow_resume_current: bool = True,
    max_work_seconds: int = 900,
    interrupt_policy: str = "pause_and_resume",
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = now_ms or int(time.time() * 1000)
    next_heartbeat_seconds = _next_heartbeat_seconds(status=status, user_active=user_active)
    base = {
        "plan_id": str(uuid.uuid4()),
        "generated_at_ts": now_ms,
        "next_heartbeat_seconds": next_heartbeat_seconds,
        "interrupt_policy": interrupt_policy,
        "mode": mode,
    }
    if user_active and status in {"idle", "busy", "draining"}:
        return {
            **base,
            "action": "yield_to_user",
            "reason": "user-active work is present; pause passive/background work",
            "resume": current_task,
            "lease": None,
        }
    if status == "offline":
        return {**base, "action": "standby", "reason": "agent reported offline", "lease": None}
    if status == "interrupted":
        return {
            **base,
            "action": "pause_current_and_wait_for_user_work",
            "reason": "agent reported interruption; preserve current task and prioritize new interactive work",
            "resume": current_task,
            "lease": None,
        }
    if status == "paused":
        return {**base, "action": "stay_paused", "reason": "agent is paused by operator or local policy", "resume": current_task, "lease": None}
    if status == "busy":
        return {
            **base,
            "action": "continue_current",
            "reason": "agent is already busy; do not pull passive backlog work",
            "resume": current_task,
            "lease": _advisory_lease(current_task.get("task_id") if current_task else None, mode, now_ms, max_work_seconds),
        }
    if status == "draining":
        return {
            **base,
            "action": "finish_current_step_then_pause",
            "reason": "agent is draining; finish the smallest safe checkpoint and do not start new work",
            "resume": current_task,
            "lease": _advisory_lease(current_task.get("task_id") if current_task else None, mode, now_ms, min(max_work_seconds, 300)),
        }
    if allow_resume_current and current_task:
        return {
            **base,
            "action": "resume_current",
            "reason": "current passive task exists; resume it before pulling new work",
            "recommended_task_id": current_task.get("task_id"),
            "resume": current_task,
            "lease": _advisory_lease(current_task.get("task_id"), mode, now_ms, max_work_seconds),
        }
    if not candidates:
        return {**base, "action": "idle_wait", "reason": "no eligible passive work found", "lease": None}
    recommended = candidates[0]
    if mode == "claim_ready":
        return {
            **base,
            "action": "recommend_claim_via_approved_endpoint",
            "reason": "eligible passive work found; caller may claim only through approved task-claim API",
            "recommended_task_id": recommended.get("task_id"),
            "lease": _advisory_lease(recommended.get("task_id"), mode, now_ms, max_work_seconds),
            "safety": recommended.get("safety"),
        }
    return {
        **base,
        "action": "review_next_candidate",
        "reason": "eligible passive work found; review-only/dry-run recommendation",
        "recommended_task_id": recommended.get("task_id"),
        "lease": _advisory_lease(recommended.get("task_id"), mode, now_ms, max_work_seconds),
        "safety": recommended.get("safety"),
    }


def _next_heartbeat_seconds(status: str, user_active: bool = False) -> int:
    if user_active:
        return 10
    return {
        "idle": 45,
        "busy": 60,
        "paused": 120,
        "interrupted": 10,
        "offline": 300,
        "draining": 20,
    }.get(status, 45)


def _advisory_lease(task_id: Optional[str], mode: str, now_ms: int, max_work_seconds: int) -> dict[str, Any] | None:
    if not task_id:
        return None
    seconds = max(60, min(int(max_work_seconds or 900), 7200))
    return {
        "lease_id": str(uuid.uuid4()),
        "task_id": task_id,
        "mode": mode,
        "lease_type": "advisory_only",
        "issued_at_ts": now_ms,
        "expires_at_ts": now_ms + (seconds * 1000),
        "max_work_seconds": seconds,
        "claim_required_before_execution": True,
    }


def _suggest_idle_work(
    neo_factory: Callable[[], Any],
    capabilities: list[str],
    limit: int,
    current_task_id: Optional[str] = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    neo = None
    now_ms = now_ms or int(time.time() * 1000)
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
                {"current_task_id": current_task_id, "limit": int(limit) * 5},
            )
            out: list[dict[str, Any]] = []
            for row in rows:
                task = dict(row["t"])
                item = _normalize_idle_candidate(task, now_ms=now_ms)
                if _is_passive_safe(item) and _capabilities_match(item, capabilities):
                    item["rank_score"] = _rank_candidate(item, now_ms)
                    item["why"] = _candidate_reason(item)
                    item["safety"] = _candidate_safety(item)
                    out.append(item)
            out.sort(key=lambda item: item.get("rank_score", 999999))
            return out[:limit]
    except Exception:
        return []
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def _normalize_idle_candidate(task: dict[str, Any], now_ms: int | None = None) -> dict[str, Any]:
    now_ms = now_ms or int(time.time() * 1000)
    payload = _json_dict(task.get("payload_json") or task.get("payload"))
    metadata = _json_dict(task.get("metadata_json") or task.get("metadata"))
    required = task.get("required_capabilities") or payload.get("required_capabilities") or []
    if isinstance(required, str):
        required = [part.strip() for part in required.split(",") if part.strip()]
    privacy = str(task.get("privacy") or task.get("privacy_label") or payload.get("privacy") or metadata.get("privacy") or "").lower()
    queue_class = str(payload.get("queue_class") or task.get("queue_class") or metadata.get("queue_class") or "background")
    title = str(task.get("title") or payload.get("title") or task.get("id") or "AssistX task")
    created_at_ts = _int_or_none(task.get("created_at_ts") or payload.get("created_at_ts"))
    updated_at_ts = _int_or_none(task.get("updated_at_ts") or payload.get("updated_at_ts"))
    age_seconds = max(0, int((now_ms - (created_at_ts or updated_at_ts or now_ms)) / 1000))
    return {
        "task_id": str(task.get("id") or task.get("task_id") or title),
        "title": title,
        "status": str(task.get("status") or "UNKNOWN"),
        "kind": task.get("kind") or payload.get("kind"),
        "priority": task.get("priority") or payload.get("priority"),
        "queue_class": queue_class,
        "required_capabilities": required if isinstance(required, list) else [],
        "privacy": privacy,
        "local_only": bool(task.get("local_only") or payload.get("local_only") or metadata.get("local_only") or privacy in {"local_only", "private", "secret"}),
        "sensitive": bool(task.get("sensitive") or payload.get("sensitive") or metadata.get("sensitive") or privacy in {"private", "secret", "voice_auth", "enrollment", "enrollment_sample"}),
        "created_at_ts": created_at_ts,
        "updated_at_ts": updated_at_ts,
        "age_seconds": age_seconds,
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


def _rank_candidate(item: dict[str, Any], now_ms: int) -> int:
    priority = str(item.get("priority") or "").upper()
    queue = str(item.get("queue_class") or "background").lower()
    status = str(item.get("status") or "").upper()
    score = 1000
    score += {"HIGH": -250, "MEDIUM": -150, "LOW": -50}.get(priority, 0)
    score += {"batch": -70, "backlog": -60, "background": -40, "docs": -30}.get(queue, 0)
    score += {"REVIEW": -30, "READY": 0}.get(status, 0)
    score -= min(int(item.get("age_seconds") or 0) // 3600, 72)
    score += len(item.get("required_capabilities") or []) * 5
    return score


def _candidate_reason(item: dict[str, Any]) -> str:
    queue = item.get("queue_class") or "background"
    status = item.get("status") or "UNKNOWN"
    required = item.get("required_capabilities") or []
    caps = f"; requires {', '.join(required)}" if required else ""
    return f"{status} {queue} task is passive-safe{caps}."


def _candidate_safety(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "passive_safe": True,
        "local_only": bool(item.get("local_only")),
        "sensitive": bool(item.get("sensitive")),
        "privacy": item.get("privacy") or "unspecified",
        "requires_claim_before_execution": True,
        "write_allowed": False,
    }


def _record_agent_heartbeat(
    neo_factory: Callable[[], Any],
    agent_id: str,
    status: str,
    capabilities: list[str],
    current_task_id: Optional[str],
    current_focus: Optional[str],
    last_completed_task_id: Optional[str],
    last_result_summary: Optional[str],
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
                    a.last_completed_task_id=$last_completed_task_id,
                    a.last_result_summary=$last_result_summary,
                    a.mode=$mode,
                    a.plan_json=$plan_json,
                    a.metadata_json=$metadata_json,
                    a.last_seen_at=datetime(),
                    a.last_seen_at_ts=$now_ms,
                    a.next_heartbeat_seconds=$next_heartbeat_seconds,
                    a.recommended_task_id=$recommended_task_id,
                    a.action=$action
                """,
                {
                    "agent_id": agent_id,
                    "status": status,
                    "capabilities": capabilities,
                    "current_task_id": current_task_id,
                    "current_focus": current_focus,
                    "last_completed_task_id": last_completed_task_id,
                    "last_result_summary": (last_result_summary or "")[:1000] if last_result_summary else None,
                    "mode": mode,
                    "plan_json": json.dumps(plan, sort_keys=True),
                    "metadata_json": json.dumps(metadata or {}, sort_keys=True),
                    "now_ms": int(now_ms),
                    "next_heartbeat_seconds": int(plan.get("next_heartbeat_seconds") or 45),
                    "recommended_task_id": plan.get("recommended_task_id"),
                    "action": plan.get("action"),
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


def _read_agent_heartbeats(neo_factory: Callable[[], Any], limit: int) -> list[dict[str, Any]]:
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            rows = s.run(
                """
                MATCH (a:AgentHeartbeat)
                RETURN a
                ORDER BY coalesce(a.last_seen_at_ts, 0) DESC
                LIMIT $limit
                """,
                {"limit": int(limit)},
            )
            out = []
            for row in rows:
                item = dict(row["a"])
                item["plan"] = _json_dict(item.get("plan_json"))
                item.pop("plan_json", None)
                out.append(item)
            return out
    except Exception:
        return []
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def _heartbeat_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(items)}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


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


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None
