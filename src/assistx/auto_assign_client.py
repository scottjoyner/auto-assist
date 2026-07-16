from __future__ import annotations

import logging
import os
from uuid import uuid4

from .config import settings
from .outbox_client import OutboxClient

logger = logging.getLogger(__name__)

# W-24: durable SQLite-backed outbox so notify_task_created retries/queue
# instead of fire-and-forget.
_outbox = OutboxClient(
    db_path=os.getenv("ASSISTX_OUTBOX_DB", os.path.expanduser("~/.assistx_outbox.db")),
    api_url=settings.auto_assign_base_url,
    api_user=os.getenv("AUTO_ASSIGN_AUTH_USER", ""),
    api_pass=os.getenv("AUTO_ASSIGN_AUTH_PASS", ""),
)



def notify_task_created(
    task_id: str,
    correlation_id: str | None = None,
    title: str = "",
    required_capabilities: list[str] | None = None,
    kind: str | None = None,
) -> bool:
    """POST a ``task.candidate.created`` event to auto-assign.

    Returns ``True`` when the event was accepted (HTTP 2xx), ``False``
    when auto-assign is unreachable, misconfigured, or the URL is not set.
    """
    base = settings.auto_assign_base_url
    if not base:
        return False

    cid = correlation_id or uuid4().hex
    body = {
        "event_id": f"evt_{uuid4().hex}",
        "event_type": "task.candidate.created",
        "source_repo": "auto-assist",
        "source_service": "assistx",
        "idempotency_key": f"assistx-task-created:{task_id}",
        "subject": {"task_id": task_id},
        "payload": {
            "task_id": task_id,
            "title": title,
            "kind": kind,
            "required_capabilities": required_capabilities or [],
        },
        "correlation_id": cid,
        "links": {"correlation_id": cid, "task_id": task_id},
    }
    try:
        # W-24: enqueue into the durable outbox; the outbox thread retries
        # delivery so a transient auto-assign outage does not lose the event.
        _outbox.enqueue(body)
        logger.info("queued task.candidate.created for task %s into outbox", task_id)
        return True
    except Exception as e:  # noqa: BLE001 — never let notify crash the caller
        logger.warning("failed to enqueue auto-assign notify for task %s: %s", task_id, e)
        return False
