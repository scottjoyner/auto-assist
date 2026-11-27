#!/usr/bin/env python3
"""Read-only queue-health JSON CLI for any AssistX outbox SQLite store.

Every health field is derived by *importing and calling* ``read_queue_health()``
from ``src/assistx/outbox_client.py``; the derivations are not reimplemented
here. Before that call, the CLI itself opens ``file:<path>?mode=ro`` and enables
``PRAGMA query_only=ON``, and checks that the ``outbox`` table exists on that
same read-only connection. Nothing is ever inserted, updated, deleted, claimed
or marked, and payload columns are never selected or printed.

stdout is exactly one compact, key-sorted JSON object. Exit status 0 means the
queue was read successfully -- this is not a readiness verdict (a stale queue,
``ok: false``, still exits 0). Exit status 1 means an unreadable database, a
missing database or a missing ``outbox`` table; 2 is argparse usage errors.

The ``reason`` field is sanitised: raw sqlite exception text (which could embed
a path or other operator-supplied text) is replaced by a fixed label, so no
payload body or credential-shaped value can reach stdout or stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# The CLI lives in <repo>/tools; make <repo>/src importable without installation.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from assistx.outbox_client import read_queue_health  # noqa: E402

DEFAULT_TABLE = "outbox"
_GENERIC_ERROR = "outbox unreadable"
_REASON_OK = re.compile(
    r"^(?:outbox database not present|"
    r"oldest pending event is \d+(?:\.\d+)?h old \(threshold \d+(?:\.\d+)?h\))$"
)


def _safe_reason(reason: object) -> str | None:
    """Keep only the two known-good reason formats; drop raw sqlite error text."""
    if reason is None:
        return None
    text = str(reason)
    if _REASON_OK.fullmatch(text):
        return text
    return _GENERIC_ERROR


def _open_read_only(path: Path) -> tuple[sqlite3.Connection, int]:
    """Open *path* as ``file:...?mode=ro`` and switch on ``query_only``."""
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA query_only=ON")
    query_only = conn.execute("PRAGMA query_only").fetchone()[0]
    return conn, int(query_only)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Check for the expected table/view without reading any row of it."""
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed


def _unreadable_health() -> dict[str, Any]:
    """Fixed zeroed shape used only if the imported function itself raises."""
    return {
        "status": "error",
        "ok": True,  # matches read_queue_health: unreadable != unready
        "reason": _GENERIC_ERROR,
        "stale_after_s": None,
        "pending": 0,
        "failed": 0,
        "claimed": 0,
        "delivered": 0,
        "dead": 0,
        "quarantined": 0,
        "claimable_pending": 0,
        "total": 0,
        "oldest_pending_at": None,
        "oldest_pending_age_s": None,
    }


def _payload(
    resolved: Path,
    health: dict[str, Any],
    *,
    query_only: int | None,
    readable: bool,
) -> dict[str, Any]:
    """Merge the imported health result with this CLI's own read-only evidence."""
    payload: dict[str, Any] = {k: v for k, v in health.items() if k != "db_path"}
    payload["db"] = str(resolved)
    payload["query_only"] = query_only
    payload["readable"] = bool(readable)
    payload.setdefault("reason", None)
    payload["reason"] = _safe_reason(payload["reason"])
    return payload


def _emit(payload: dict[str, Any]) -> None:
    """Emit exactly one compact, key-sorted JSON object on stdout."""
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def inspect(db_path: str, *, now: datetime | None = None) -> tuple[dict[str, Any], int]:
    """Inspect one database; return ``(payload, exit_code)``. Never writes."""
    resolved = Path(db_path).expanduser().resolve()
    query_only: int | None = None
    table_ok = False
    try:
        conn, query_only = _open_read_only(resolved)
        try:
            table_ok = _table_exists(conn, DEFAULT_TABLE)
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError):
        table_ok = False

    try:
        health = read_queue_health(str(resolved), now=now)
    except (sqlite3.Error, OSError, ValueError):
        # Defensive only: the imported function normally converts these to a dict.
        health = _unreadable_health()

    readable = table_ok and health.get("status") not in ("absent", "error")
    payload = _payload(resolved, health, query_only=query_only, readable=readable)
    return payload, 0 if readable else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only queue-health JSON for an AssistX outbox SQLite store.")
    parser.add_argument("db_path", help="path to the outbox SQLite database")
    parser.add_argument(
        "--now",
        metavar="ISO8601",
        help="reference time for staleness (default: current UTC); supply to make output reproducible",
    )
    args = parser.parse_args(argv)
    try:
        now = _parse_now(args.now)
    except ValueError:
        parser.error("--now must be an ISO-8601 timestamp")
        return 2  # pragma: no cover - argparse.error exits

    payload, code = inspect(args.db_path, now=now)
    _emit(payload)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
