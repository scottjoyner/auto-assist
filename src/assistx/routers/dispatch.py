"""Dispatch router (W-18 extraction from api.py).

Routes:
  POST /api/dispatch
  POST /api/dispatches/{dispatch_id}/reassign
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException

from ..api import DispatchIn, DispatchTarget, auth, _neo, get_paperclip_client


router = APIRouter(tags=["dispatch"])


@router.post("/api/dispatch")
def api_create_dispatch(body: DispatchIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        pc = get_paperclip_client()
        result = neo.create_dispatch_with_paperclip(
            task_id=body.task_id,
            target=body.target.model_dump(),
            priority=body.priority,
            idempotency_key=body.idempotency_key,
            paperclip_client=pc,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        neo.close()


@router.post("/api/dispatches/{dispatch_id}/reassign")
def api_reassign_dispatch(dispatch_id: str, target: DispatchTarget, user: str = Depends(auth)):
    neo = _neo()
    try:
        with neo._session() as s:
            s.run(
                "MATCH (d:Dispatch {id:$id}) "
                "SET d.paperclip_agent_id=$agent_id, d.updated_at=datetime(), d.updated_at_ts=timestamp()",
                {"id": dispatch_id, "agent_id": target.paperclip_agent_id},
            ).consume()
            if target.paperclip_agent_id:
                session_id = uuid.uuid4().hex
                s.run(
                    "MATCH (d:Dispatch {id:$did}) "
                    "MERGE (a:AgentSession {paperclip_agent_id:$aid}) "
                    "ON CREATE SET a.id=$sid, a.created_at=datetime(), a.created_at_ts=timestamp() "
                    "MERGE (d)-[:ASSIGNED_TO]->(a)",
                    {"aid": target.paperclip_agent_id, "did": dispatch_id, "sid": session_id},
                ).consume()
        return {"dispatch_id": dispatch_id, "reassigned": True}
    finally:
        neo.close()


def build_dispatch_router() -> APIRouter:
    return router
