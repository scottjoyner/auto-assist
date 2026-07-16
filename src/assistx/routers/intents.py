"""Intents + context packets router (W-18 extraction from api.py).

Routes:
  POST /api/intents
  POST /api/brain/context
  GET  /api/context-packets/{packet_id}
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..api import (
    ContextPacketIn,
    IntentIn,
    _intent_outcome_and_confidence,
    _intent_policy_action,
    auth,
    _neo,
    classify_text,
)
from ..metrics import CONTEXT_PACKETS


router = APIRouter(tags=["intents"])


@router.post("/api/intents")
def api_create_intent(body: IntentIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        classification = classify_text(body.text)
        intent_outcome, intent_confidence = _intent_outcome_and_confidence(body.text, classification)
        policy_action = _intent_policy_action(intent_outcome, intent_confidence)
        metadata = {**(body.metadata or {}), "policy_action": policy_action}
        intent_id = neo.upsert_intent(
            source=body.source,
            text=body.text,
            idempotency_key=body.idempotency_key,
            client_ts=body.client_ts,
            metadata=metadata,
            classification=classification,
            intent_outcome=intent_outcome,
            intent_confidence=intent_confidence,
        )
        return {
            "intent_id": intent_id,
            "classification": classification,
            "intent_outcome": intent_outcome,
            "intent_confidence": intent_confidence,
            "policy_action": policy_action,
        }
    finally:
        neo.close()


@router.post("/api/brain/context")
def api_create_context_packet(body: ContextPacketIn, user: str = Depends(auth)):
    neo = _neo()
    try:
        packet = neo.create_context_packet(
            query=body.query,
            task_id=body.task_id,
            session_id=body.session_id,
            max_items=body.max_items,
            include_sources=body.include_sources or ["memory", "knowledge", "orchestration"],
        )
        CONTEXT_PACKETS.inc()
        return {"context_packet": packet}
    finally:
        neo.close()


@router.get("/api/context-packets/{packet_id}")
def api_get_context_packet(packet_id: str, user: str = Depends(auth)):
    neo = _neo()
    try:
        packet = neo.get_context_packet(packet_id)
        if not packet:
            raise HTTPException(status_code=404, detail="ContextPacket not found")
        return {"context_packet": packet}
    finally:
        neo.close()


def build_intents_router() -> APIRouter:
    return router
