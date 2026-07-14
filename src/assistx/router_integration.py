from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

from fastapi import APIRouter, Query, Request

from .coordination_metadata import build_task_candidate_metadata


def build_router_integration_router(neo_factory: Callable[[], Any]) -> APIRouter:
    """Read-only AssistX endpoints consumed by auto-router.

    These endpoints intentionally do not claim tasks, mutate workflow state, or
    dispatch work. They expose graph/context facts that auto-router can use for
    routing, dry-run backlog selection, and dashboard visibility.
    """

    router = APIRouter(prefix="/api/router", tags=["auto-router"])

    @router.get("/context-projection")
    def context_projection(request: Request) -> dict[str, Any]:
        base_url = _base_url(request)
        graph = _graph_summary(neo_factory)
        services = _service_projection(base_url)
        providers = _merge_providers(_provider_projection(base_url), _live_provider_projection(neo_factory))
        return {
            "revision": f"assistx-{int(time.time())}",
            "source": "assistx",
            "generated_at": int(time.time()),
            "nodes": _node_projection(base_url, graph),
            "providers": providers,
            "services": services,
            "metadata": {
                "projection_version": "router-context-v1",
                "read_only": True,
                "graph": graph,
                "notes": "AssistX owns task/context state; auto-router consumes this projection for routing and dry-run planning.",
            },
        }

    @router.get("/backlog-candidates")
    def backlog_candidates(
        limit: int = Query(25, ge=1, le=250),
        queue: str = Query("backlog"),
        dry_run: bool = Query(True),
    ) -> dict[str, Any]:
        items = _read_backlog_candidates(neo_factory, limit=limit, queue=queue)
        return {
            "tasks": items,
            "count": len(items),
            "queue": queue,
            "dry_run": bool(dry_run),
            "read_only": True,
            "notes": "These candidates are not claimed or mutated. auto-router may select/skip them in dry-run mode only.",
        }

    @router.get("/status")
    def router_integration_status(request: Request) -> dict[str, Any]:
        base_url = _base_url(request)
        graph = _graph_summary(neo_factory)
        return {
            "ok": True,
            "source": "assistx",
            "base_url": base_url,
            "endpoints": {
                "context_projection": f"{base_url}/api/router/context-projection",
                "backlog_candidates": f"{base_url}/api/router/backlog-candidates",
                "event_sink": f"{base_url}/api/events",
                "health": f"{base_url}/health",
            },
            "graph": graph,
        }

    return router


def _base_url(request: Request) -> str:
    explicit = os.getenv("ASSISTX_PUBLIC_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    return str(request.base_url).rstrip("/")


def _graph_summary(neo_factory: Callable[[], Any]) -> dict[str, Any]:
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            labels = {row["label"] for row in s.run("CALL db.labels() YIELD label RETURN label")}
            task_row = s.run(
                """
                MATCH (t:Task)
                RETURN
                  count(t) AS total,
                  sum(CASE WHEN t.status='READY' THEN 1 ELSE 0 END) AS ready,
                  sum(CASE WHEN t.status='REVIEW' THEN 1 ELSE 0 END) AS review,
                  sum(CASE WHEN t.status='RUNNING' THEN 1 ELSE 0 END) AS running,
                  sum(CASE WHEN t.status='DONE' THEN 1 ELSE 0 END) AS done,
                  sum(CASE WHEN t.status='FAILED' THEN 1 ELSE 0 END) AS failed
                """
            ).single()
            service_row = s.run("MATCH (s:ServiceEndpoint) RETURN count(s) AS count").single() if "ServiceEndpoint" in labels else None
            agent_row = s.run("MATCH (a:Agent) RETURN count(a) AS count").single() if "Agent" in labels else None
        return {
            "neo4j": "online",
            "tasks": {
                "total": _int_row(task_row, "total"),
                "ready": _int_row(task_row, "ready"),
                "review": _int_row(task_row, "review"),
                "running": _int_row(task_row, "running"),
                "done": _int_row(task_row, "done"),
                "failed": _int_row(task_row, "failed"),
            },
            "services": _int_row(service_row, "count"),
            "agent_clis": _int_row(agent_row, "count"),
        }
    except Exception as exc:
        return {"neo4j": "degraded", "error": str(exc)[:500]}
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def _node_projection(base_url: str, graph: dict[str, Any]) -> list[dict[str, Any]]:
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    paperclip_url = os.getenv("PAPERCLIP_API_URL", "http://host.docker.internal:3100/api")
    return [
        {
            "node_id": "assistx-api",
            "display_name": "AssistX API",
            "lane": "paperclip",
            "local": True,
            "running": True,
            "capabilities": ["context_projection", "event_sink", "backlog_candidates", "task_state"],
            "detail": "AssistX API and router context projection",
            "services": [
                _service("assistx.health", "AssistX Health", f"{base_url}/health", "health", node_id="assistx-api", status="online"),
                _service("assistx.events", "AssistX Event Sink", f"{base_url}/api/events", "event_sink", health_url=f"{base_url}/health", node_id="assistx-api"),
                _service("assistx.context", "AssistX Router Context", f"{base_url}/api/router/context-projection", "context_projection", node_id="assistx-api", status="online"),
                _service("assistx.backlog", "AssistX Backlog Candidates", f"{base_url}/api/router/backlog-candidates", "backlog_candidates", node_id="assistx-api", status="online"),
            ],
        },
        {
            "node_id": "assistx-neo4j",
            "display_name": "AssistX Neo4j",
            "lane": "local",
            "local": True,
            "running": graph.get("neo4j") == "online",
            "capabilities": ["graph", "task_state", "memory", "context"],
            "detail": "Neo4j database behind AssistX",
            "services": [_service("assistx.neo4j.bolt", "AssistX Neo4j Bolt", neo4j_uri, "graph_db", node_id="assistx-neo4j")],
        },
        {
            "node_id": "assistx-redis",
            "display_name": "AssistX Redis",
            "lane": "local",
            "local": True,
            "running": True,
            "capabilities": ["queue", "cache"],
            "detail": "AssistX Redis queue/cache",
            "services": [_service("assistx.redis", "AssistX Redis", redis_url, "queue", node_id="assistx-redis")],
        },
        {
            "node_id": "paperclip",
            "display_name": "Paperclip Control Plane",
            "lane": "paperclip",
            "local": True,
            "running": bool(paperclip_url),
            "capabilities": ["agent_dispatch", "hermes_local"],
            "detail": "Current cutover execution path for non-realtime task execution",
            "services": [_service("paperclip.api", "Paperclip API", paperclip_url, "agent_control", node_id="paperclip")],
        },
    ]


def _provider_projection(base_url: str) -> list[dict[str, Any]]:
    return [
        {
            "provider_id": "assistx",
            "provider": "assistx",
            "lane": "paperclip",
            "local": True,
            "can_use_free_api": False,
            "blocked": False,
            "node_id": "assistx-api",
            "aliases": ["assistx/context", "assistx/backlog"],
            "capabilities": ["context_projection", "event_sink", "backlog_candidates"],
            "detail": "Graph-backed router context and read-only task intake",
            "services": [
                _service("assistx.context.provider", "AssistX Router Context", f"{base_url}/api/router/context-projection", "context_projection", provider="assistx", status="online"),
                _service("assistx.backlog.provider", "AssistX Backlog Candidates", f"{base_url}/api/router/backlog-candidates", "backlog_candidates", provider="assistx", status="online"),
            ],
        },
        {
            "provider_id": "paperclip",
            "provider": "paperclip",
            "lane": "paperclip",
            "local": True,
            "can_use_free_api": False,
            "blocked": False,
            "node_id": "paperclip",
            "aliases": ["paperclip/hermes-local"],
            "capabilities": ["agent_dispatch", "terminal", "code_execution"],
            "detail": "Paperclip/Hermes remains the approved execution path during cutover",
        },
        {
            "provider_id": "lmstudio-local",
            "provider": "lmstudio-local",
            "lane": "local",
            "local": True,
            "can_use_free_api": False,
            "blocked": False,
            "node_id": "local-lmstudio",
            "aliases": ["auto/local", "auto/private"],
            "capabilities": ["chat", "local_only", "privacy"],
            "detail": "Local LM Studio fallback lane; concrete endpoint comes from auto-router provider config",
        },
        {
            "provider_id": "cerebras",
            "provider": "cerebras",
            "lane": "free_api",
            "local": False,
            "can_use_free_api": True,
            "blocked": False,
            "node_id": "cerebras-wse3",
            "aliases": ["auto/flash-start"],
            "capabilities": ["chat", "low_latency", "flash_planning"],
            "detail": "Fast free-tier flash-start planning lane when auto-router policy allows cloud use",
        },
    ]


def _live_provider_projection(neo_factory: Callable[[], Any]) -> list[dict[str, Any]]:
    neo = None
    try:
        neo = neo_factory()
        projection = neo.export_context_projection()
        providers = projection.get("providers") or []
        return [provider for provider in providers if isinstance(provider, dict)]
    except Exception:
        return []
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def _merge_providers(static: list[dict[str, Any]], live: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    def key_for(provider: dict[str, Any]) -> str:
        return str(provider.get("provider_id") or provider.get("provider") or provider.get("node_id") or "unknown")

    for provider in static + live:
        if not isinstance(provider, dict):
            continue
        merged[key_for(provider)] = provider
    return list(merged.values())


def _service_projection(base_url: str) -> list[dict[str, Any]]:
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    paperclip_url = os.getenv("PAPERCLIP_API_URL", "http://host.docker.internal:3100/api")
    return [
        _service("assistx.ui", "AssistX UI", base_url, "assistx_ui", health_url=f"{base_url}/health", priority=10, status="online"),
        _service("assistx.health", "AssistX Health", f"{base_url}/health", "health", priority=11, status="online"),
        _service("assistx.events", "AssistX Event Sink", f"{base_url}/api/events", "event_sink", health_url=f"{base_url}/health", priority=12),
        _service("assistx.context", "AssistX Router Context Projection", f"{base_url}/api/router/context-projection", "context_projection", priority=13, status="online"),
        _service("assistx.backlog", "AssistX Backlog Candidates", f"{base_url}/api/router/backlog-candidates", "backlog_candidates", priority=14, status="online"),
        _service("assistx.neo4j.bolt", "AssistX Neo4j Bolt", neo4j_uri, "graph_db", priority=30),
        _service("assistx.redis", "AssistX Redis", redis_url, "queue", priority=40),
        _service("paperclip.api", "Paperclip API", paperclip_url, "agent_control", priority=50),
    ]


def _read_backlog_candidates(neo_factory: Callable[[], Any], limit: int, queue: str) -> list[dict[str, Any]]:
    neo = None
    try:
        neo = neo_factory()
        with neo.driver.session() as s:
            rows = s.run(
                """
                MATCH (t:Task)
                WHERE t.status IN ['READY','REVIEW']
                RETURN t
                ORDER BY coalesce(t.created_at_ts, t.updated_at_ts, 0) ASC
                LIMIT $limit
                """,
                {"limit": int(limit)},
            )
            candidates: list[dict[str, Any]] = []
            for row in rows:
                task = dict(row["t"])
                normalized = _normalize_task_candidate(task)
                if _queue_matches(normalized, queue):
                    candidates.append(normalized)
            return candidates[:limit]
    except Exception:
        return []
    finally:
        try:
            if neo is not None:
                neo.close()
        except Exception:
            pass


def _normalize_task_candidate(task: dict[str, Any]) -> dict[str, Any]:
    payload = _json_dict(task.get("payload_json") or task.get("payload"))
    metadata = _json_dict(task.get("metadata_json") or task.get("metadata"))
    privacy = str(task.get("privacy") or task.get("privacy_label") or payload.get("privacy") or metadata.get("privacy") or "").lower()
    queue_class = str(payload.get("queue_class") or task.get("queue_class") or metadata.get("queue_class") or "background")
    priority = _priority_for_queue(queue_class, task.get("priority") or payload.get("priority"))
    local_only = bool(task.get("local_only") or payload.get("local_only") or metadata.get("local_only") or privacy in {"local_only", "private", "secret"})
    sensitive = bool(task.get("sensitive") or payload.get("sensitive") or metadata.get("sensitive") or privacy in {"private", "secret", "voice_auth", "enrollment", "enrollment_sample"})
    allow_cloud = False if local_only else bool(payload.get("allow_cloud", metadata.get("allow_cloud", True)))
    title = str(task.get("title") or payload.get("title") or task.get("id") or "AssistX task")
    prompt = str(payload.get("prompt") or payload.get("description") or task.get("description") or title)
    candidate = {
        "task_id": str(task.get("id") or task.get("task_id") or title),
        "title": title,
        "prompt": prompt,
        "model": str(payload.get("model") or metadata.get("model") or "auto/backlog-burn"),
        "priority": priority,
        "local_only": local_only,
        "allow_cloud": allow_cloud,
        "sensitive": sensitive,
        "max_completion_tokens": int(payload.get("max_completion_tokens") or metadata.get("max_completion_tokens") or 700),
        "status": task.get("status"),
        "queue": queue_class,
        "privacy": privacy,
        "metadata": {
            **metadata,
            "assistx_source": True,
            "assistx_raw_status": task.get("status"),
            "assistx_queue": queue_class,
            "source_task_id": task.get("id") or task.get("task_id"),
        },
    }
    candidate["metadata"] = build_task_candidate_metadata(candidate, candidate["metadata"])
    return candidate


def _queue_matches(task: dict[str, Any], queue: str) -> bool:
    requested = (queue or "backlog").lower()
    q = str(task.get("queue") or "background").lower()
    if requested in {"all", "any"}:
        return True
    if requested in {"backlog", "background", "batch"}:
        return q in {"backlog", "background", "batch", "unknown"}
    return q == requested


def _priority_for_queue(queue_class: str, raw_priority: Any) -> str:
    if raw_priority:
        raw = str(raw_priority).lower()
        if raw in {"critical", "repo_critical", "interactive", "batch", "background", "local_only"}:
            return raw
        if raw in {"low", "deferred", "idle"}:
            return "background"
        if raw in {"normal", "medium"}:
            return "batch"
    q = (queue_class or "background").lower()
    if q in {"batch", "backlog"}:
        return "batch"
    if q == "critical":
        return "critical"
    if q == "interactive":
        return "interactive"
    return "background"


def _service(
    service_id: str,
    name: str,
    url: str,
    service_type: str,
    *,
    health_url: str | None = None,
    node_id: str | None = None,
    provider: str | None = None,
    status: str = "unknown",
    priority: int = 100,
) -> dict[str, Any]:
    return {
        "service_id": service_id,
        "name": name,
        "url": url,
        "health_url": health_url or url,
        "service_type": service_type,
        "node_id": node_id,
        "provider": provider,
        "status": status,
        "tags": [service_type, "assistx"],
        "detail": f"AssistX-projected {service_type} service",
        "priority": priority,
    }


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


def _int_row(row: Any, key: str) -> int:
    try:
        value = row[key] if row else 0
        return int(value or 0)
    except Exception:
        return 0
