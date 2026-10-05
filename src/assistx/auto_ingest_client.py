"""auto_ingest event client (W-29).

Emits ``ingest.evidence.linked`` / ``context.available`` events via the durable
outbox so auto-ingest can later connect to the unified fleet without a hard
dependency on AssistX availability. Mirrors ``auto_assign_client``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from uuid import uuid4

from .outbox_client import OutboxClient

logger = logging.getLogger(__name__)

_outbox = OutboxClient(
    db_path=os.getenv("ASSISTX_OUTBOX_DB", os.path.expanduser("~/.assistx_outbox.db")),
    # Auto-ingest events are consumed by the canonical AssistX event sink. Do
    # not route them through the retired/optional auto-assign endpoint.
    api_url=os.getenv("ASSISTX_API_URL", ""),
    api_user=os.getenv("ASSISTX_AUTH_USER", os.getenv("AUTO_INGEST_AUTH_USER", "")),
    api_pass=os.getenv("ASSISTX_AUTH_PASS", os.getenv("AUTO_INGEST_AUTH_PASS", "")),
)


def _enqueue(event_type: str, payload: dict, correlation_id: str | None = None) -> bool:
    cid = correlation_id or str(uuid4())
    event_id = f"evt_{uuid4().hex}"
    node_id = str(payload.get("node_id") or os.getenv("ASSISTX_NODE_ID", "auto-ingest"))
    links = []
    if payload.get("node_id"):
        links.append({"rel": "FOR_NODE", "target_type": "Node", "target_id": str(payload["node_id"])})
    if payload.get("evidence_ref"):
        links.append({"rel": "REFERENCES", "target_type": "Evidence", "target_id": str(payload["evidence_ref"])})
    if payload.get("context_packet_id"):
        links.append({"rel": "REFERENCES", "target_type": "ContextPacket", "target_id": str(payload["context_packet_id"])})
    body = {
        "event_id": event_id,
        "event_type": event_type,
        "source_repo": "auto-assist",
        "source_service": "assistx",
        "node_id": node_id,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": f"{event_type}:{cid}",
        "schema_version": "2026-08.v1",
        "subject": {"kind": "event", "id": event_id},
        "payload": payload,
        "artifact_refs": [],
        "metadata": {},
        "privacy": {"pii": False, "privacy_class": "internal", "retention_class": "keep"},
        "correlation_id": cid,
        "links": links,
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
