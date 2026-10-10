"""Bounded, storage-neutral read-only search over validated fleet trace metadata.

No index files, raw payload retrieval, user identity, or network operations.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Iterable

from trace_metadata_contract import (
    IDENTIFIER, OPERATIONS, SOURCES, STATUSES, validate_envelope,
)

FILTER_FIELDS = frozenset({"node_id", "session_id", "trace_id", "producer", "status", "operation"})
MAX_ROWS = 5_000
MAX_PAGE = 100


def _cursor_for(key: tuple[str, str]) -> str:
    payload = json.dumps([key[0], key[1]], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _parse_cursor(value: str | None) -> tuple[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError("invalid cursor")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        data = json.loads(decoded)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if not isinstance(data, list) or len(data) != 2 or not all(isinstance(x, str) for x in data):
        raise ValueError("invalid cursor shape")
    if not IDENTIFIER.fullmatch(data[1]) or len(data[0]) > 35 or not data[0].endswith("Z"):
        raise ValueError("invalid cursor fields")
    return (data[0], data[1])


def _filters(requested: dict[str, str]) -> dict[str, str]:
    if set(requested) - FILTER_FIELDS:
        raise ValueError("unsupported query filter")
    for k, v in requested.items():
        if not isinstance(v, str) or len(v) > 128 or not v:
            raise ValueError("invalid query value")
        if k in {"node_id", "session_id", "trace_id"} and not IDENTIFIER.fullmatch(v):
            raise ValueError("invalid query identifier")
        if k == "producer" and v not in SOURCES:
            raise ValueError("unsupported producer")
        if k == "status" and v not in STATUSES:
            raise ValueError("unsupported status")
        if k == "operation" and v not in OPERATIONS:
            raise ValueError("unsupported operation")
    return requested


def search_metadata(
    rows: Iterable[dict[str, Any]], *,
    filters: dict[str, str] | None = None, cursor: str | None = None,
    limit: int = 25, maximum_rows: int = MAX_ROWS,
) -> dict[str, Any]:
    """Fail closed if input budget exceeded; stable (UTC timestamp, event id) pagination."""
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise ValueError("invalid page size")
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= MAX_ROWS:
        raise ValueError("invalid scan budget")
    criteria = _filters(filters or {})
    after = _parse_cursor(cursor)
    seen: dict[str, dict[str, Any]] = {}
    candidates = []
    scanned = 0
    for row in rows:
        scanned += 1
        if scanned > maximum_rows:
            raise ValueError("scan budget exceeded; not complete")
        canonical = validate_envelope(row)
        eid = canonical["event_id"]
        if eid in seen:
            if seen[eid] != canonical:
                raise ValueError("conflicting duplicate event identity")
            continue
        seen[eid] = canonical
        if any(canonical.get(key) != value for key, value in criteria.items()):
            continue
        key = (canonical["occurred_at"], eid)
        if after is None or key > after:
            candidates.append((key, canonical))
    candidates.sort(key=lambda item: item[0])
    selected = candidates[:limit]
    next_cursor = _cursor_for(selected[-1][0]) if len(candidates) > limit else None
    return {
        "items": [record for _, record in selected],
        "next_cursor": next_cursor,
        "rows_scanned": scanned,
        "matched_after_cursor": len(candidates),
        "complete_fleet_coverage": False,
        "source_authentication_verified": False,
        "persistent_index_created": False,
    }
