from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, Query


def build_passive_event_router(neo_factory: Callable[[], Any], auth_dependency: Any | None = None) -> APIRouter:
    """Read-only passive coordination event history."""

    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []
    router = APIRouter(prefix="/api/agents", tags=["passive-events"], dependencies=dependencies)

    @router.get("/passive-events")
    def passive_events(
        agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = Query(50, ge=1, le=250),
    ) -> dict[str, Any]:
        items = read_passive_events(
            neo_factory,
            agent_id=agent_id,
            task_id=task_id,
            event_type=event_type,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "summary": passive_event_summary(items),
            "read_only": True,
        }

    return router


def record_passive_event(
    neo_factory: Callable[[], Any],
    event_type: str,
    *,
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
    claim_id: Optional[str] = None,
    lease_id: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
    summary: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> str | None:
    """Best-effort append-only event log for passive coordination.

    Event logging must never block heartbeat/claim flows. This function catches
    Neo4j errors and returns None if the event could not be recorded.
    """

    event_id = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as session:
            session.run(
                """
                CREATE (e:PassiveAgentEvent {
                    event_id:$event_id,
                    event_type:$event_type,
                    agent_id:$agent_id,
                    task_id:$task_id,
                    claim_id:$claim_id,
                    lease_id:$lease_id,
                    status:$status,
                    action:$action,
                    result:$result,
                    summary:$summary,
                    metadata_json:$metadata_json,
                    created_at:datetime(),
                    created_at_ts:$created_at_ts
                })
                WITH e
                OPTIONAL MATCH (a:AgentHeartbeat {agent_id:$agent_id})
                FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END | MERGE (a)-[:EMITTED_PASSIVE_EVENT]->(e))
                WITH e
                OPTIONAL MATCH (t:Task)
                WHERE coalesce(t.id, t.task_id) = $task_id
                FOREACH (_ IN CASE WHEN t IS NULL THEN [] ELSE [1] END | MERGE (e)-[:ABOUT_TASK]->(t))
                """,
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "claim_id": claim_id,
                    "lease_id": lease_id,
                    "status": status,
                    "action": action,
                    "result": result,
                    "summary": (summary or "")[:1000] if summary else None,
                    "metadata_json": json.dumps(metadata or {}, sort_keys=True),
                    "created_at_ts": now_ms,
                },
            ).consume()
        return event_id
    except Exception:
        return None
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def read_passive_events(
    neo_factory: Callable[[], Any],
    *,
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as session:
            rows = session.run(
                """
                MATCH (e:PassiveAgentEvent)
                WHERE ($agent_id IS NULL OR e.agent_id = $agent_id)
                  AND ($task_id IS NULL OR e.task_id = $task_id)
                  AND ($event_type IS NULL OR e.event_type = $event_type)
                RETURN e
                ORDER BY coalesce(e.created_at_ts, 0) DESC
                LIMIT $limit
                """,
                {"agent_id": agent_id, "task_id": task_id, "event_type": event_type, "limit": int(limit)},
            )
            out: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row["e"])
                item["metadata"] = _json_dict(item.get("metadata_json"))
                item.pop("metadata_json", None)
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


def passive_event_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_agent: dict[str, int] = {}
    for item in items:
        event_type = str(item.get("event_type") or "unknown")
        agent_id = str(item.get("agent_id") or "unknown")
        by_type[event_type] = by_type.get(event_type, 0) + 1
        by_agent[agent_id] = by_agent.get(agent_id, 0) + 1
    return {"total": len(items), "by_type": by_type, "by_agent": by_agent}


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
