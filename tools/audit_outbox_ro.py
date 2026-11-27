#!/usr/bin/env python3
"""Read-only, schema-aware audit of an AssistX outbox SQLite store.

Safety properties (this script is intentionally stdlib-only and standalone so it
can be copied to a router-side host without installing AssistX):

* The database is opened as ``file:<path>?mode=ro`` and ``PRAGMA query_only=ON``
  is enabled immediately. No INSERT / UPDATE / DELETE / DDL is ever issued; no
  rows are claimed, deleted or marked.
* Only explicit, whitelisted columns are selected. Payload bodies
  (``payload_json`` and friends) are never read, let alone printed.
* ``claim_owner`` is treated as opaque. Only recognised ``quarantine:`` markers
  are rendered; every other non-null owner (i.e. a claim token, which may be
  credential-looking) is counted but printed as ``<redacted>``.
* Exit status 0 means the audit completed (it is not a health verdict).
  Exit status 1 means the database could not be read or the expected table is
  absent. Usage errors exit 2 via argparse.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_TABLE = "outbox"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UNSAFE_TEXT = re.compile(r"[^A-Za-z0-9._:\-]")
_MAX_LABEL = 160
_QUARANTINE_PREFIX = "quarantine:"
_REDACTED = "<redacted>"

# Known interpretive columns. Presence is detected from PRAGMA table_info at run
# time so older and newer outbox schemas can both be audited.
STATUS_COLUMNS = ("status",)
ATTEMPT_COLUMNS = ("attempt_count",)
OWNER_COLUMNS = ("claim_owner",)
TIMESTAMP_COLUMNS = ("created_at", "claimed_at", "last_attempt_at", "updated_at")


def _quote(identifier: str) -> str:
    """Return a double-quoted SQL identifier, rejecting anything unexpected."""
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")
    return '"' + identifier + '"'


def _safe_label(value: object) -> str:
    """Render a group value without emitting control chars or payload-looking text."""
    if value is None:
        return "NULL"
    text = str(value)
    if not text:
        return "<empty>"
    if _UNSAFE_TEXT.search(text):
        return _REDACTED
    if len(text) > _MAX_LABEL:
        return text[:_MAX_LABEL] + "...(truncated)"
    return text


def _owner_label(value: object) -> str:
    """Only quarantine markers are printable; other owners are opaque tokens."""
    if value is None:
        return "NULL"
    text = str(value)
    if text.lower().startswith(_QUARANTINE_PREFIX):
        return _safe_label(text)
    return _REDACTED


def _is_timestamp(name: str) -> bool:
    return name in TIMESTAMP_COLUMNS or name.endswith("_at") or name.endswith("_ts")


def _audit(db_path: str, table: str) -> int:
    resolved = Path(db_path).expanduser().resolve()
    uri = resolved.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        query_only = conn.execute("PRAGMA query_only").fetchone()[0]

        stat = os.stat(resolved)
        mtime = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")

        print(f"Database: {resolved}")
        print(f"Open mode: mode=ro, query_only={query_only} (ON=1)")
        print(f"File: mtime={mtime} size_bytes={stat.st_size}")

        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
        ).fetchone()
        if found is None:
            print(f"error: expected table {table!r} is absent", file=sys.stderr)
            return 1

        quoted = _quote(table)
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")]

        status_cols = [c for c in columns if c in STATUS_COLUMNS]
        attempt_cols = [c for c in columns if c in ATTEMPT_COLUMNS]
        owner_cols = [c for c in columns if c in OWNER_COLUMNS]
        timestamp_cols = [c for c in columns if _is_timestamp(c)]

        print("Detected columns:")
        print(f"  status: {', '.join(status_cols) if status_cols else 'absent'}")
        print(f"  attempt_count: {', '.join(attempt_cols) if attempt_cols else 'absent'}")
        print(f"  claim_owner: {', '.join(owner_cols) if owner_cols else 'absent'}")
        print(f"  timestamps: {', '.join(timestamp_cols) if timestamp_cols else 'absent'}")

        total = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        print(f"Total rows: {total}")

        if status_cols:
            col = _quote(status_cols[0])
            rows = conn.execute(
                f"SELECT {col}, COUNT(*) FROM {quoted} GROUP BY {col} ORDER BY COUNT(*) DESC, {col}"
            ).fetchall()
            print("Status distribution:")
            for value, count in rows:
                print(f"  {_safe_label(value)}: {count}")
        else:
            print("Status distribution: unavailable (column absent)")

        if attempt_cols:
            col = _quote(attempt_cols[0])
            rows = conn.execute(f"SELECT {col}, COUNT(*) FROM {quoted} GROUP BY {col} ORDER BY {col}").fetchall()
            print("Attempt distribution:")
            for value, count in rows:
                print(f"  {_safe_label(value)}: {count}")
        else:
            print("Attempt distribution: unavailable (column absent)")

        if owner_cols:
            col = _quote(owner_cols[0])
            rows = conn.execute(
                f"SELECT {col}, COUNT(*) FROM {quoted} GROUP BY {col} ORDER BY COUNT(*) DESC"
            ).fetchall()
            quarantine_total = 0
            print("Claim-owner counts (non-quarantine owners redacted):")
            for value, count in rows:
                label = _owner_label(value)
                print(f"  {label}: {count}")
                if value is not None and str(value).lower().startswith(_QUARANTINE_PREFIX):
                    quarantine_total += count
            print(f"Quarantine markers total: {quarantine_total}")
        else:
            print("Claim-owner counts: unavailable (column absent)")

        if timestamp_cols:
            print("Timestamp ranges:")
            for column in timestamp_cols:
                col = _quote(column)
                low, high = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {quoted}").fetchone()
                print(f"  {column}: min={_safe_label(low)} max={_safe_label(high)}")

        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only, schema-aware outbox audit (never writes, claims or prints payloads)."
    )
    parser.add_argument("db_path", help="path to the outbox SQLite database")
    parser.add_argument("--table", default=DEFAULT_TABLE, help=f"outbox table name (default: {DEFAULT_TABLE})")
    args = parser.parse_args(argv)

    if not _IDENTIFIER.fullmatch(args.table):
        print("error: --table must be a plain SQL identifier", file=sys.stderr)
        return 2

    try:
        return _audit(args.db_path, args.table)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"error: cannot audit database: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
