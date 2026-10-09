"""Preview-only, keyset-paged TraceEvent projections. Draft, disabled in routes by default.

No full event hydration or trace payload reads in timeline pages. Metadata is
unverified producer evidence, not physical actor attestation or full custody.
"""
import base64
import binascii
import json
import re
from typing import Any, Optional

from neo4j import Query

PAGE_MAX = 100
PREVIEW_MAX = 4096
_CURSOR_VERSION = 1
_ID_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,320}$")
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,720}={0,2}$")


class InvalidTraceCursor(ValueError):
    pass


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise InvalidTraceCursor("Invalid event identifier")
    return value


def encode_cursor(ts_ms: int, event_id: str) -> str:
    if type(ts_ms) is not int or not 0 <= ts_ms < 2**63:
        raise InvalidTraceCursor("Invalid event timestamp")
    obj = {"v": _CURSOR_VERSION, "t": ts_ms, "id": _safe_id(event_id)}
    binary = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return base64.urlsafe_b64encode(binary).decode("ascii").rstrip("=")


def decode_cursor(raw: Optional[str]) -> Optional[dict]:
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > 720 or not _CURSOR_PATTERN.fullmatch(raw):
        raise InvalidTraceCursor("Invalid pagination cursor")
    try:
        data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
        if len(data) > 512:
            raise InvalidTraceCursor("Oversized pagination cursor")
        def no_duplicates(pairs):
            obj = {}
            for k,v in pairs:
                if k in obj: raise InvalidTraceCursor("Duplicate cursor attribute")
                obj[k] = v
            return obj
        obj = json.loads(data, object_pairs_hook=no_duplicates)
        if not isinstance(obj, dict) or set(obj) != {"v","t","id"} or type(obj["v"]) is not int or obj["v"] != _CURSOR_VERSION:
            raise InvalidTraceCursor("Unsupported pagination cursor")
        return {"ts_ms": _valid_ts(obj["t"]), "event_id": _safe_id(obj["id"])}
    except (binascii.Error, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise InvalidTraceCursor("Invalid pagination cursor") from exc


def _valid_ts(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise InvalidTraceCursor("Invalid event timestamp")
    return value


def _safe_scalar(value: Any, length: int = 160) -> Optional[str]:
    if not isinstance(value, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    return value[:length]


def get_trace_page(neo, correlation_id: str, *, limit: int = 80, cursor: Optional[str] = None) -> dict:
    """Return at most 100 ordered event metadata rows; no payload or implicit count."""
    _safe_id(correlation_id)
    if type(limit) is not int or not 1 <= limit <= PAGE_MAX:
        raise ValueError("Invalid page size")
    before = decode_cursor(cursor)
    params = {"correlation_id": correlation_id, "take": limit + 1,
              "before_ts": before["ts_ms"] if before else None,
              "before_id": before["event_id"] if before else None}
    cypher = """
    MATCH (g:TraceGroup {correlation_id:$correlation_id})-[:HAS_EVENT]->(t:TraceEvent)
    WHERE t.ts_ms IS NOT NULL AND t.event_id IS NOT NULL
      AND ($before_ts IS NULL OR t.ts_ms < $before_ts
        OR (t.ts_ms = $before_ts AND t.event_id < $before_id))
    RETURN t.event_id AS event_id, t.ts_ms AS ts_ms,
           substring(coalesce(t.event_type,''),0,160) AS event_type,
           substring(coalesce(t.source,''),0,160) AS source,
           substring(coalesce(t.task_id,''),0,160) AS task_id,
           substring(coalesce(t.dispatch_id,''),0,160) AS dispatch_id,
           substring(coalesce(t.route_id,''),0,160) AS route_id,
           substring(coalesce(t.assignment_id,''),0,160) AS assignment_id
    ORDER BY ts_ms DESC, event_id DESC
    LIMIT $take
    """
    with neo._session() as session:
        result = session.run(Query(cypher, timeout=4.0), params)
        records = list(result)
    if len(records) > limit + 1:
        raise RuntimeError("Trace page exceeded query limit")
    events = []
    for row in records[:limit]:
        # Defensive allowlist: do not return additional columns even if an
        # upstream query/projection changes during subsequent refactors.
        item = {key: row[key] for key in (
            "event_id","ts_ms","event_type","source","task_id",
            "dispatch_id","route_id","assignment_id")}
        item["event_id"] = _safe_id(item["event_id"])
        item["ts_ms"] = _valid_ts(item["ts_ms"])
        for key in ("event_type","source","task_id","dispatch_id","route_id","assignment_id"):
            item[key] = _safe_scalar(item.get(key))
        events.append(item)
    keys = [(event["ts_ms"], event["event_id"]) for event in events]
    if len(set(keys)) != len(keys) or keys != sorted(keys, reverse=True):
        raise RuntimeError("Ambiguous timeline cursor ordering")
    has_more = len(records) > limit
    if has_more and events:
        extra = records[limit]
        extra_key = (_valid_ts(extra["ts_ms"]), _safe_id(extra["event_id"]))
        if extra_key >= keys[-1]:
            raise RuntimeError("Ambiguous timeline page boundary")
    return {
        "schema": "trace-event-page-v1", "correlation_id": correlation_id,
        "events": events, "returned": len(events), "page_size_max": PAGE_MAX,
        "has_more": has_more,
        "next_cursor": encode_cursor(events[-1]["ts_ms"], events[-1]["event_id"])
            if has_more and events else None,
        "total_indexed_events": None,
        "source_snapshot_immutable": False,
        "historical_retention_proven": False,
        "metadata_only": True,
        "physical_node_identity_verified": False,
    }


def get_trace_payload_preview(neo, correlation_id: str, event_id: str) -> Optional[dict]:
    """Called only by an explicitly operator-initiated POST endpoint."""
    _safe_id(correlation_id)
    _safe_id(event_id)
    cypher = """
    MATCH (g:TraceGroup {correlation_id:$correlation_id})-[:HAS_EVENT]->(t:TraceEvent {event_id:$event_id})
    RETURN substring(coalesce(t.payload_json,''),0,$preview_chars) AS preview,
           size(coalesce(t.payload_json,'')) AS payload_chars
    LIMIT 2
    """
    with neo._session() as session:
        records = list(session.run(Query(cypher, timeout=4.0), {
            "correlation_id": correlation_id, "event_id": event_id,
            "preview_chars": PREVIEW_MAX}))
    if not records:
        return None
    if len(records) != 1:
        raise RuntimeError("Ambiguous event ownership")
    row = records[0]
    preview = row["preview"]
    if not isinstance(preview, str) or len(preview) > PREVIEW_MAX:
        raise RuntimeError("Invalid preview projection")
    length = row["payload_chars"]
    if type(length) is not int or length < 0:
        raise RuntimeError("Invalid preview size")
    return {"schema":"trace-payload-preview-v1",
            "correlation_id":correlation_id,
            "event_id":event_id,
            "payload_preview":preview,
            "preview_chars":len(preview),
            "truncated":length > len(preview),
            "payload_complete": length <= len(preview),
            "source_authenticated":False,
            "historical_retention_proven":False}
