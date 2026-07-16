"""auto_ingest event client (W-29).

Emits ``ingest.evidence.linked`` / ``context.available`` events via the durable
outbox so auto-ingest can later connect to the unified fleet without a hard
dependency on AssistX availability. Mirrors ``auto_assign_client``.
"""

from __future__ import annotations

import logging
import os
from uuid import uuid4

from .config import settings
from .outbox_client import OutboxClient

logger = logging.getLogger(__name__)

_outbox = OutboxClient(
    db_path=os.getenv("ASSISTX_OUTBOX_DB", os.path.expanduser("~/.assistx_outbox.db")),
    api_url=settings.auto_assign_base_url,
    api_user=os.getenv("AUTO_INGEST_AUTH_USER", ""),
    api_pass=os.getenv("AUTO_INGEST_AUTH_PASS", ""),
)


def _enqueue(event_type: str, payload: dict, correlation_id: str | None = None) -> bool:
    cid = correlation_id or uuid4().hex
    body = {
        "event_id": f"evt_{uuid4().hex}",
        "event_type": event_type,
        "source_repo": "auto-assist",
        "source_service": "assistx",
        "correlation_id": cid,
        "payload": payload,
        "links": {"correlation_id": cid},
    }
    try:
        _outbox.enqueue(body)
        logger.info("queued %s (correlation_id=%s) into outbox", event_type, cid)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to enqueue %s: %s", event_type, e)
        return False


def notify_evidence_linked(
    node_id: str,
    evidence_ref: str,
    enrichment_kind: str | None = None,
    correlation_id: str | None = None,
) -> bool:
    """Emit ``ingest.evidence.linked`` — auto-ingest linked evidence to a graph node."""
    return _enqueue(
        "ingest.evidence.linked",
        {
            "node_id": node_id,
            "evidence_ref": evidence_ref,
            "enrichment_kind": enrichment_kind,
        },
        correlation_id=correlation_id,
    )


def notify_context_available(
    context_packet_id: str,
    summary: str | None = None,
    correlation_id: str | None = None,
) -> bool:
    """Emit ``context.available`` — a new enrichment/context packet is ready for consumers."""
    return _enqueue(
        "context.available",
        {
            "context_packet_id": context_packet_id,
            "summary": summary,
        },
        correlation_id=correlation_id,
    )
