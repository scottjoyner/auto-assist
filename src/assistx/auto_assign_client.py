from __future__ import annotations

import logging
import os
from uuid import uuid4

import requests

from .config import settings

logger = logging.getLogger(__name__)


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

    url = f"{base.rstrip('/')}/api/events"
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
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        logger.info("notified auto-assign of task %s (status %s)", task_id, resp.status_code)
        return True
    except requests.RequestException as e:
        logger.warning("failed to notify auto-assign about task %s: %s", task_id, e)
        return False
