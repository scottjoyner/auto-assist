"""Offline notification reconciliation; never enqueue, deliver or read live graph.

Inputs are owner-supplied point-in-time exports, not authority by themselves.
Only aggregate counts are returned. Actual source completeness/receipt
provenance must be independently established before any replay approval.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

SCHEMA = "assistx.autoassign-notification-audit.v1"
TASK_SCHEMA = "assistx.task-identity-snapshot.v1"
RECEIVER_SCHEMA = "autoassign.inbound-key-snapshot.v1"
EXPECTED_EVENT = "task.candidate.created"
CAPTURE_AGE_LIMIT = timedelta(minutes=15)
CAPTURE_SKEW_LIMIT = timedelta(minutes=5)


def _snapshot_items(data: dict, *, schema: str, field: str, now: datetime) -> list[str]:
    if not isinstance(data, dict) or data.get("schema") != schema or data.get("complete") is not True:
        raise ValueError("incomplete or unrecognized source-declared snapshot")
    when = datetime.fromisoformat(str(data.get("captured_at", "")))
    if when.tzinfo is None:
        raise ValueError("snapshot timestamp is not timezone-aware")
    age = now - when.astimezone(UTC)
    if age > CAPTURE_AGE_LIMIT or age < -timedelta(minutes=1):
        raise ValueError("snapshot is stale or from the future")
    items = data.get(field)
    if not isinstance(items, list) or not all(
        isinstance(x, str) and x.strip() == x and bool(x) for x in items
    ):
        raise ValueError("snapshot identities must be nonempty strings")
    if len(items) > 100_000 or len(items) != len(set(items)):
        raise ValueError("snapshot has duplicated or oversized identities")
    return items


def _read_offline_outbox(path: str) -> list[dict[str, Any]]:
    """Read outbox rows from a caller-supplied SQLite COPY, never emit payloads."""
    if not Path(path).is_file():
        raise ValueError("offline SQLite snapshot does not exist")
    uri = "file:" + quote(str(Path(path).resolve()), safe="/") + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=3)) as conn:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute(
                "SELECT event_id, status, payload_json FROM outbox"
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("offline outbox snapshot unavailable") from exc
    result = []
    for event_id, status, raw in rows:
        try:
            event = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("outbox event not valid JSON") from exc
        if not isinstance(event, dict):
            raise ValueError("outbox event is not an object")
        if event.get("event_type") != EXPECTED_EVENT:
            continue
        if status not in ("pending", "failed", "dead", "delivered"):
            raise ValueError("unrecognized outbox event status")
        key = event.get("idempotency_key")
        subject = event.get("subject")
        task = subject.get("task_id") if isinstance(subject, dict) else None
        if (
            not isinstance(event_id, str)
            or not isinstance(key, str) or not isinstance(task, str)
            or key != f"assistx-task-created:{task}"
        ):
            raise ValueError("outbox task/key provenance mismatch")
        result.append({"key": key, "event_id": event_id, "status": status})
    return result


def audit(
    tasks: dict[str, Any],
    inbound: dict[str, Any],
    offline_outbox_path: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return aggregate evidence categories, NEVER automatic replay authority."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("audit clock must be timezone-aware")
    now = now.astimezone(UTC)
    ids = _snapshot_items(tasks, schema=TASK_SCHEMA, field="task_ids", now=now)
    keys = _snapshot_items(
        inbound, schema=RECEIVER_SCHEMA, field="idempotency_keys", now=now
    )
    if not ids:
        raise ValueError("empty task inventory cannot establish coverage")
    when_task = datetime.fromisoformat(tasks["captured_at"])
    when_inbound = datetime.fromisoformat(inbound["captured_at"])
    if abs(when_task - when_inbound) > CAPTURE_SKEW_LIMIT:
        raise ValueError("snapshots captured too far apart")
    known_tasks = set(ids)
    inbound_set = set(keys)
    rows = _read_offline_outbox(offline_outbox_path)
    local = {}
    for row in rows:
        key = row["key"]
        task_id = key.removeprefix("assistx-task-created:")
        if task_id not in known_tasks:
            continue  # Different known historical task; cannot prove coverage.
        local.setdefault(key, []).append(row)

    counts: Counter[str] = Counter()
    for task_id in ids:
        key = f"assistx-task-created:{task_id}"
        records = local.get(key, [])
        if len(records) > 1:
            bucket = "HOLD_LOCAL_DUPLICATE"
        elif key in inbound_set:
            bucket = "RECEIVER_INGEST_WITNESS"
        elif not records:
            bucket = "HOLD_NO_NOTIFICATION_WITNESS"
        elif records[0]["status"] in ("pending", "failed"):
            bucket = "LOCAL_PENDING_ONLY"
        elif records[0]["status"] == "dead":
            bucket = "HOLD_DEAD_LOCAL"
        else:
            bucket = "HOLD_DELIVERED_LOCAL_NOT_RECEIVER_ATTESTED"
        counts[bucket] += 1

    complete = counts["RECEIVER_INGEST_WITNESS"] == len(ids)
    return {
        "schema": SCHEMA,
        "status": (
            "PASS_SOURCE_DECLARED_INGEST_COVERAGE_ONLY"
            if complete else "HOLD_INCOMPLETE_NOTIFICATION_WITNESS"
        ),
        "task_id_count": len(ids),
        "category_counts": dict(sorted(counts.items())),
        "snapshots_source_declared_not_independently_authenticated": True,
        "receiver_ingest_is_not_task_processing": True,
        "task_identifiers_or_payloads_emitted": False,
        "automatic_reenqueue_or_network_effects": 0,
        "production_release_authorized": False,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Offline source-declared task notification coverage check"
    )
    parser.add_argument("--task-snapshot", required=True)
    parser.add_argument("--receiver-snapshot", required=True)
    parser.add_argument("--outbox-sqlite-copy", required=True)
    args = parser.parse_args()
    try:
        tasks = json.loads(Path(args.task_snapshot).read_text())
        inbound = json.loads(Path(args.receiver_snapshot).read_text())
        result = audit(tasks, inbound, args.outbox_sqlite_copy)
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        result = {
            "schema": SCHEMA,
            "status": "HOLD_UNVERIFIABLE_OFFLINE_INPUTS",
            "production_release_authorized": False,
            "automatic_reenqueue_or_network_effects": 0,
        }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS_SOURCE_DECLARED_INGEST_COVERAGE_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
