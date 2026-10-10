"""Storage-neutral, metadata-only AssistX fleet trace envelope (research contract).

This validator does not authenticate sources, store data, or admit a trace into
production. Source identity must be established outside user-supplied JSON.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

SCHEMA = "assistx.fleet.trace.v1"
SOURCES = frozenset({"assistx", "opencode", "hermes", "kipnerter", "worker"})
OPERATIONS = frozenset({"session-start", "session-end", "model", "tool", "delegate",
                        "approval", "retry", "heartbeat", "context", "error"})
STATUSES = frozenset({"started", "ok", "failed", "denied", "timeout", "unknown"})
REQUIRED = frozenset({"schema", "event_id", "trace_id", "span_id", "session_id",
                      "node_id", "producer", "operation", "status", "occurred_at", "sequence"})
OPTIONAL = frozenset({"parent_span_id", "parent_session_id", "model_id", "git_sha",
                      "artifact_sha256", "duration_ms"})
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA = re.compile(r"^[0-9a-f]{64}$")


def _utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 35 or not value.endswith("Z"):
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return stamp.tzinfo == timezone.utc and stamp.year >= 2020


def validate_envelope(row: Any) -> dict[str, Any]:
    """Reject raw body/custody hints and unknown authority; return a canonical copy."""
    if not isinstance(row, dict):
        raise ValueError("envelope must be an object")
    if set(row) - (REQUIRED | OPTIONAL):
        raise ValueError("unapproved metadata field")
    if REQUIRED - set(row):
        raise ValueError("required lineage field missing")
    if len(json.dumps(row, ensure_ascii=True).encode("utf-8")) > 4096:
        raise ValueError("envelope exceeds metadata budget")
    if row["schema"] != SCHEMA:
        raise ValueError("schema version unsupported")
    for key in ("event_id", "trace_id", "span_id", "session_id", "node_id"):
        if not isinstance(row[key], str) or not IDENTIFIER.fullmatch(row[key]):
            raise ValueError("invalid lineage identifier: " + key)
    for key in ("parent_span_id", "parent_session_id", "model_id"):
        if row.get(key) is not None and (
            not isinstance(row[key], str) or not IDENTIFIER.fullmatch(row[key])
        ):
            raise ValueError("invalid optional identifier: " + key)
    if row["producer"] not in SOURCES or row["operation"] not in OPERATIONS:
        raise ValueError("unrecognized producer or operation")
    if row["status"] not in STATUSES:
        raise ValueError("unknown status")
    if not _utc_timestamp(row["occurred_at"]):
        raise ValueError("invalid UTC event timestamp")
    if type(row["sequence"]) is not int or row["sequence"] < 0:
        raise ValueError("invalid event sequence")
    if "duration_ms" in row and (
        type(row["duration_ms"]) is not int or not 0 <= row["duration_ms"] <= 86_400_000
    ):
        raise ValueError("duration outside budget")
    for key in ("git_sha", "artifact_sha256"):
        if row.get(key) is None:
            continue
        value = row[key]
        if not isinstance(value, str):
            raise ValueError("digest must be a string")
        if key == "artifact_sha256" and not SHA.fullmatch(value):
            raise ValueError("invalid artifact digest")
        if key == "git_sha" and not re.fullmatch(r"[a-f0-9]{7,40}", value):
            raise ValueError("invalid git SHA")
    return dict(row)


def inspect_lineage(rows: list[dict[str, Any]]) -> dict[str, int | bool]:
    """Metadata-only coverage: never claim missing parents are verified."""
    identifiers: set[tuple[str, str]] = set()
    ids: dict[str, dict[str, Any]] = {}
    accepted = []
    duplicates = 0
    for row in rows:
        record = validate_envelope(row)
        if record["event_id"] in ids:
            if ids[record["event_id"]] != record:
                raise ValueError("conflicting duplicate event ID")
            duplicates += 1
            continue
        ids[record["event_id"]] = record
        identifiers.add((record["trace_id"], record["span_id"]))
        accepted.append(record)
    missing_parent_spans = sum(
        1 for row in accepted if row.get("parent_span_id") and
        (row["trace_id"], row["parent_span_id"]) not in identifiers
    )
    return {"events": len(accepted), "duplicate_events": duplicates,
            "distinct_nodes": len({x["node_id"] for x in accepted}),
            "distinct_sessions": len({x["session_id"] for x in accepted}),
            "distinct_traces": len({x["trace_id"] for x in accepted}),
            "missing_parent_spans": missing_parent_spans,
            "complete_fleet_coverage": False,
            "source_authentication_verified": False}
