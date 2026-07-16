"""Review router (W-18 extraction from api.py).

Routes:
  GET  /api/review/tasks
  GET  /api/review/audit
  GET  /api/review/audit/summary
  POST /api/review/tasks/{task_id}/approve
  POST /api/review/tasks/{task_id}/reject
  POST /api/review/tasks/{task_id}/clarify
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import time as _time

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api import (
    ReviewDecisionIn,
    PAPERCLIP_AGENT_ID,
    auth,
    _neo,
    get_paperclip_client,
    _json_dict,
)


router = APIRouter(tags=["review"])


@router.get("/api/review/tasks")
def api_list_review_tasks(
    status: Optional[str] = Query("REVIEW", description="Task status filter"),
    policy_action: Optional[str] = Query(None, description="Intent policy action filter"),
    review_decision: Optional[str] = Query(None, description="Review decision filter"),
    limit: int = Query(50, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        if status == "REVIEW":
            items = neo.get_review_tasks(limit=limit)
        else:
            items = neo.get_tasks_by_status(status=status or "REVIEW", limit=limit)
        normalized = []
        for item in items:
            payload = _json_dict(item.get("payload_json"))
            inferred_policy = payload.get("policy_action")
            if inferred_policy and not item.get("policy_action"):
                item["policy_action"] = inferred_policy
            normalized.append(item)

        if policy_action:
            normalized = [i for i in normalized if (i.get("policy_action") or "") == policy_action]
        if review_decision:
            normalized = [i for i in normalized if (i.get("review_decision") or "") == review_decision]

        normalized = normalized[:limit]
        return {"items": normalized, "count": len(normalized)}
    finally:
        neo.close()


@router.get("/api/review/audit")
def api_review_audit(
    cursor: Optional[str] = Query(None, description="Opaque cursor: '<reviewed_at_ts>:<task_id>'"),
    limit: int = Query(100, ge=1, le=500),
    user: str = Depends(auth),
):
    neo = _neo()
    try:
        cursor_ts: Optional[int] = None
        cursor_id: Optional[str] = None
        if cursor:
            try:
                ts_part, id_part = cursor.split(":", 1)
                cursor_ts = int(ts_part)
                cursor_id = id_part
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid cursor format")
        with neo._session() as s:
            where_cursor = ""
            params: Dict[str, Any] = {"limit": limit}
            if cursor_ts is not None and cursor_id is not None:
                where_cursor = (
                    " AND (coalesce(r.reviewed_at_ts, r.updated_at_ts, r.created_at_ts, 0) < $cursor_ts "
                    "OR (coalesce(r.reviewed_at_ts, r.updated_at_ts, r.created_at_ts, 0) = $cursor_ts AND r.id < $cursor_id)) "
                )
                params["cursor_ts"] = cursor_ts
                params["cursor_id"] = cursor_id
            res = s.run(
                """
                MATCH (r:Task)
                WHERE r.kind='intent_review' AND r.review_decision IS NOT NULL
                """ + where_cursor + """
                OPTIONAL MATCH (r)-[:APPROVED_AS]->(approved:Task)
                RETURN r, approved
                ORDER BY coalesce(r.reviewed_at_ts, r.updated_at_ts, r.created_at_ts, 0) DESC
                LIMIT $limit
                """,
                params,
            )
            items: List[Dict[str, Any]] = []
            for row in res:
                review_task = dict(row["r"])
                payload = _json_dict(review_task.get("payload_json"))
                approved = dict(row["approved"]) if row["approved"] else None
                reviewed_ts = review_task.get("reviewed_at_ts") or review_task.get("updated_at_ts") or review_task.get("created_at_ts")
                items.append(
                    {
                        "review_task_id": review_task.get("id"),
                        "review_decision": review_task.get("review_decision"),
                        "review_note": review_task.get("review_note"),
                        "reviewed_by": review_task.get("reviewed_by"),
                        "reviewed_at_ts": reviewed_ts,
                        "status": review_task.get("status"),
                        "policy_action": review_task.get("policy_action") or payload.get("policy_action"),
                        "source_intent": payload.get("source_intent"),
                        "source_text": payload.get("source_text") or review_task.get("title"),
                        "approved_task_id": approved.get("id") if approved else None,
                        "approved_task_status": approved.get("status") if approved else None,
                    }
                )
        next_cursor = None
        if items:
            last = items[-1]
            if last.get("reviewed_at_ts") is not None and last.get("review_task_id"):
                next_cursor = f"{int(last['reviewed_at_ts'])}:{last['review_task_id']}"
        return {"items": items, "count": len(items), "next_cursor": next_cursor}
    finally:
        neo.close()


@router.get("/api/review/audit/summary")
def api_review_audit_summary(user: str = Depends(auth)):
    neo = _neo()
    try:
        cutoff_ts = int(_time.time() * 1000) - (24 * 60 * 60 * 1000)
        with neo._session() as s:
            rows = s.run(
                """
                MATCH (r:Task)
                WHERE r.kind='intent_review'
                  AND r.review_decision IS NOT NULL
                  AND coalesce(r.reviewed_at_ts, r.updated_at_ts, r.created_at_ts, 0) >= $cutoff_ts
                RETURN r.review_decision AS decision, count(r) AS cnt
                """,
                {"cutoff_ts": cutoff_ts},
            ).data()
        by_decision = {str(r["decision"]): int(r["cnt"]) for r in rows if r.get("decision")}
        total = sum(by_decision.values())
        return {"window_hours": 24, "total_decisions": total, "by_decision": by_decision}
    finally:
        neo.close()


@router.post("/api/review/tasks/{task_id}/approve")
def api_approve_review_task(task_id: str, body: ReviewDecisionIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        review_task = neo.get_task(task_id)
        if not review_task:
            raise HTTPException(status_code=404, detail="Review task not found")
        if review_task.get("status") != "REVIEW":
            raise HTTPException(status_code=400, detail="Task is not in REVIEW status")

        payload = _json_dict(review_task.get("payload_json"))
        source_text = payload.get("source_text") or review_task.get("title") or ""
        source_intent = payload.get("source_intent")
        policy_action = payload.get("policy_action")

        result = neo.create_task_with_context(
            title=(source_text[:120] + "...") if len(source_text) > 120 else source_text,
            task_type="task",
            kind="approved_intent",
            required_capabilities=(body.target.capabilities if body.target else None) or ["terminal"],
            target_agent_id=body.target.paperclip_agent_id if body.target else None,
            priority=body.priority,
            payload={
                "source_intent": source_intent,
                "source_text": source_text,
                "approved_from_review_task": task_id,
                "policy_action": policy_action,
                "operator_note": body.note,
            },
            context_query=source_text,
            context_sources=["memory", "knowledge", "orchestration"],
            auto_dispatch=body.auto_dispatch,
            paperclip_client=get_paperclip_client() if body.auto_dispatch else None,
            paperclip_agent_id=(
                body.target.paperclip_agent_id
                if body.target and body.target.paperclip_agent_id
                else PAPERCLIP_AGENT_ID
            ),
        )

        created_task_id = result["task_id"]
        with neo._session() as s:
            s.run(
                "MATCH (r:Task {id:$rid}), (t:Task {id:$tid}) "
                "SET r.status='DONE', "
                "    r.review_decision='approved', "
                "    r.review_note=$note, "
                "    r.reviewed_by=$user, "
                "    r.reviewed_at=datetime(), "
                "    r.reviewed_at_ts=timestamp(), "
                "    r.updated_at=datetime(), "
                "    r.updated_at_ts=timestamp() "
                "MERGE (r)-[:APPROVED_AS]->(t)",
                {"rid": task_id, "tid": created_task_id, "note": (body.note or "")[:1000], "user": user},
            ).consume()
            if source_intent:
                s.run(
                    "MATCH (i:Intent {id:$iid}), (t:Task {id:$tid}) "
                    "MERGE (i)-[:CREATED_TASK]->(t)",
                    {"iid": source_intent, "tid": created_task_id},
                ).consume()

        return {
            "review_task_id": task_id,
            "decision": "approved",
            "created_task_id": created_task_id,
            "dispatch_id": result.get("dispatch_id"),
        }
    finally:
        neo.close()


@router.post("/api/review/tasks/{task_id}/reject")
def api_reject_review_task(task_id: str, body: ReviewDecisionIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        review_task = neo.get_task(task_id)
        if not review_task:
            raise HTTPException(status_code=404, detail="Review task not found")
        if review_task.get("status") != "REVIEW":
            raise HTTPException(status_code=400, detail="Task is not in REVIEW status")
        with neo._session() as s:
            s.run(
                "MATCH (r:Task {id:$rid}) "
                "SET r.status='CANCELLED', "
                "    r.review_decision='rejected', "
                "    r.review_note=$note, "
                "    r.reviewed_by=$user, "
                "    r.reviewed_at=datetime(), "
                "    r.reviewed_at_ts=timestamp(), "
                "    r.updated_at=datetime(), "
                "    r.updated_at_ts=timestamp()",
                {"rid": task_id, "note": (body.note or "")[:1000], "user": user},
            ).consume()
        return {"review_task_id": task_id, "decision": "rejected"}
    finally:
        neo.close()


@router.post("/api/review/tasks/{task_id}/clarify")
def api_clarify_review_task(task_id: str, body: ReviewDecisionIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        review_task = neo.get_task(task_id)
        if not review_task:
            raise HTTPException(status_code=404, detail="Review task not found")
        if review_task.get("status") != "REVIEW":
            raise HTTPException(status_code=400, detail="Task is not in REVIEW status")
        with neo._session() as s:
            s.run(
                "MATCH (r:Task {id:$rid}) "
                "SET r.review_decision='clarification_requested', "
                "    r.review_note=$note, "
                "    r.reviewed_by=$user, "
                "    r.reviewed_at=datetime(), "
                "    r.reviewed_at_ts=timestamp(), "
                "    r.updated_at=datetime(), "
                "    r.updated_at_ts=timestamp()",
                {"rid": task_id, "note": (body.note or "")[:1000], "user": user},
            ).consume()
        return {"review_task_id": task_id, "decision": "clarification_requested"}
    finally:
        neo.close()


def build_review_router() -> APIRouter:
    return router
