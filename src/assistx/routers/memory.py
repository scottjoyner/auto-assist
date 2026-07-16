"""Memory router (W-18 extraction from api.py).

Routes:
  POST /api/memory/items
  GET  /api/memory
  GET  /api/memory/{memory_id}
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api import MemoryWriteIn, auth, _neo


router = APIRouter(tags=["memory"])


@router.post("/api/memory/items")
def api_write_memory(body: MemoryWriteIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        memory_id = neo.upsert_memory_item(
            kind=body.kind,
            text=body.text,
            source=body.source,
            session_id=body.session_id,
            task_id=body.task_id,
            metadata=body.metadata,
        )
        return {"memory_item_id": memory_id}
    finally:
        neo.close()


@router.get("/api/memory")
def api_list_memory(
    kind: Optional[str] = Query(None, description="filter by memory kind"),
    source: Optional[str] = Query(None, description="filter by source"),
    view: str = Query("durable", description="memory surface view: durable, diagnostics, all"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    durable_kinds = ["fact", "note", "preference", "plan", "summary"]
    diagnostic_kinds = ["outcome", "task_result", "user_profile"]
    normalized_view = (view or "durable").lower().strip()
    if normalized_view not in {"durable", "diagnostics", "all"}:
        raise HTTPException(status_code=400, detail="view must be one of: durable, diagnostics, all")
    neo = _neo()
    try:
        with neo._session() as s:
            q = "MATCH (m:MemoryItem)"
            params: dict[str, Any] = {"limit": limit}
            conditions = []
            if kind:
                conditions.append("m.kind=$kind")
                params["kind"] = kind
            elif normalized_view == "durable":
                conditions.append("m.kind IN $allowed_kinds")
                params["allowed_kinds"] = durable_kinds
            elif normalized_view == "diagnostics":
                conditions.append("m.kind IN $allowed_kinds")
                params["allowed_kinds"] = diagnostic_kinds
            if source:
                conditions.append("m.source=$source")
                params["source"] = source
            if conditions:
                q += " WHERE " + " AND ".join(conditions)
            q += " RETURN m ORDER BY m.updated_at_ts DESC LIMIT $limit"
            res = s.run(q, params)
            items = [dict(r["m"]) for r in res]
            kind_counts: dict[str, int] = {}
            for item in items:
                k = item.get("kind") or "unknown"
                kind_counts[k] = kind_counts.get(k, 0) + 1
            return {"items": items, "count": len(items), "view": normalized_view, "kind_counts": kind_counts}
    finally:
        neo.close()


@router.get("/api/memory/{memory_id}")
def api_get_memory(memory_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo._session() as s:
            rec = s.run(
                "MATCH (m:MemoryItem {id:$id}) "
                "OPTIONAL MATCH (m)<-[:WROTE_MEMORY]-(s:AgentSession) "
                "OPTIONAL MATCH (m)<-[:RELATED_MEMORY]-(t:Task) "
                "RETURN m, collect(DISTINCT s) AS sessions, collect(DISTINCT t) AS tasks",
                {"id": memory_id},
            ).single()
            if not rec:
                raise HTTPException(status_code=404, detail="Memory not found")
            memory = dict(rec["m"])
            sessions = [dict(s) for s in rec["sessions"] if s]
            tasks = [dict(t) for t in rec["tasks"] if t]
            return {"memory": memory, "sessions": sessions, "tasks": tasks}
    finally:
        neo.close()


def build_memory_router() -> APIRouter:
    return router
