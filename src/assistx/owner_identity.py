"""Owner authority for opt-in multi-user task execution.

Never use task payload, a client-supplied owner header, or executor agent
identity to establish a human/tenant principal. Only trusted ASGI scope
state from an independently verified gateway is accepted.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

OWNER_CONSTRAINT = "task_owner_idempotency_unique"

class TaskOwnerError(ValueError):
    pass


def owner_required() -> bool:
    raw = os.getenv("ASSISTX_REQUIRE_OWNER_BINDING", "false").strip().lower()
    return raw not in {"", "0", "false", "off", "no"}


def canonical_identity(value: Any, *, origin: str = "owner") -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TaskOwnerError(f"{origin} missing")
    if "owner_issuer" not in value or "owner_subject" not in value:
        raise TaskOwnerError(f"{origin} missing")
    identity = {}
    for key in ("owner_issuer", "owner_subject"):
        item = value[key]
        if (
            type(item) is not str
            or not 1 <= len(item) <= 160
            or item != item.strip()
            or any(ch.isspace() or ord(ch) < 33 for ch in item)
        ):
            raise TaskOwnerError(f"{origin} invalid")
        identity[key] = item
    return identity


def authenticated_owner(scope: Mapping[str, Any], auth_user: str) -> dict[str, str]:
    state = scope.get("state")
    principal = state.get("verified_owner") if isinstance(state, Mapping) else None
    if not isinstance(principal, Mapping) or principal.get("authenticated") is not True:
        raise TaskOwnerError("independently verified gateway owner absent")
    identity = canonical_identity(principal, origin="verified gateway owner")
    if identity["owner_subject"] != auth_user:
        raise TaskOwnerError("verified owner does not match authenticated task submitter")
    return identity


def task_owner(task: Mapping[str, Any], *, strict: bool | None = None) -> dict[str, str]:
    required = owner_required() if strict is None else strict
    if task.get("owner_issuer") is None and task.get("owner_subject") is None and not required:
        return {}
    return canonical_identity(task, origin="authoritative Task record")


def scoped_key(owner: Mapping[str, Any], idempotency_key: str) -> str:
    identity = canonical_identity(owner)
    if type(idempotency_key) is not str or not 1 <= len(idempotency_key) <= 256:
        raise TaskOwnerError("idempotency key invalid")
    # Unambiguous canonical encoding and domain separation prevent owner
    # collision and avoid storing untrusted keys or identifying data in clear.
    encoded = json.dumps([identity["owner_issuer"], identity["owner_subject"],
                          idempotency_key],separators=(",",":"),ensure_ascii=False).encode()
    return "owner:v1:" + hashlib.sha256(b"assistx-task-owner-key\0" + encoded).hexdigest()
