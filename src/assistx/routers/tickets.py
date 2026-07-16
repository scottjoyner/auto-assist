"""Tickets router (W-18 extraction from api.py).

Routes:
  POST /api/tickets
  GET  /api/tickets/{ticket_id}/tree
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..api import TicketIn, auth, _neo


router = APIRouter(tags=["tickets"])


@router.post("/api/tickets")
def api_create_ticket(body: TicketIn, user: str = Depends(auth)):
    if body.status not in {"READY", "CLAIMED", "RUNNING", "DONE", "FAILED", "CANCELLED", "REVIEW"}:
        raise HTTPException(status_code=400, detail="Unsupported ticket status")
    if body.ticket_type not in {"deliverable", "epic", "story", "task", "bug", "chore"}:
        raise HTTPException(status_code=400, detail="ticket_type must be deliverable, epic, story, task, bug, or chore")
    neo = _neo()
    try:
        ticket_id = neo.upsert_ticket(
            title=body.title,
            ticket_type=body.ticket_type,
            status=body.status,
            kind=body.kind,
            parent_id=body.parent_id,
            required_capabilities=body.required_capabilities,
            target_agent_id=body.target_agent_id,
            priority=body.priority,
            payload=body.payload,
            idempotency_key=body.idempotency_key,
        )
        return {"ticket_id": ticket_id}
    finally:
        neo.close()


@router.get("/api/tickets/{ticket_id}/tree")
def api_get_ticket_tree(ticket_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        tree = neo.get_ticket_tree(ticket_id)
        if not tree:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return tree
    finally:
        neo.close()


def build_tickets_router() -> APIRouter:
    return router
