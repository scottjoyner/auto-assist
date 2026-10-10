"""Non-authoritative W3C traceparent for authenticated mobile requests.

Trace IDs are correlation metadata only: never authenticate or admit based on
these fields. Invalid caller-supplied values are dropped, not reflected.
"""
from __future__ import annotations

import re

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def validated_traceparent(raw: str | None) -> str | None:
    if not isinstance(raw, str) or len(raw) != 55:
        return None
    matched = _TRACEPARENT.fullmatch(raw)
    if matched is None:
        return None
    trace_id, parent_id, flags = matched.groups()
    if trace_id == "0" * 32 or parent_id == "0" * 16:
        return None
    # The v00 fields are already guaranteed hex and fixed width.
    return f"00-{trace_id}-{parent_id}-{flags}"
