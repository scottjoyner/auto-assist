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
* ``--json`` emits one deterministic, key-sorted JSON object on stdout with
  the keys ``db``, ``size_bytes``, ``mtime``, ``detected_columns``,
  ``total_rows``, ``status_distribution``, ``attempt_distribution``,
  ``claim_owner_counts`` and ``pass``. ``pass`` is false on an unreadable
  database or a missing table. Without ``--json`` the human output is
  byte-for-byte unchanged; the same redaction rules apply in both modes.

* Exit status 0 means the audit completed (it is not a health verdict).
  Exit status 1 means the database could not be read or the expected table is
  absent. Usage errors exit 2 via argparse.
"""

from __future__ import annotations

import argparse
import json
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


def _empty_columns() -> dict[str, list[str]]:
    """Shape used when a column family could not be inspected (error paths)."""
    return {key: [] for key in ("status", "attempt_count", "claim_owner", "timestamps")}


def _result(
    db: str,
    *,
    size_bytes: int | None = None,
    mtime: str | None = None,
    detected_columns: dict[str, list[str]] | None = None,
    total_rows: int | None = None,
    status_distribution: dict[str, int] | None = None,
    attempt_distribution: dict[str, int] | None = None,
    claim_owner_counts: dict[str, int] | None = None,
    passed: bool = False,
) -> dict[str, object]:
    """Build the single, fixed-shape JSON payload (sorted by key when emitted)."""
    return {
        "db": db,
        "size_bytes": size_bytes,
        "mtime": mtime,
        "detected_columns": detected_columns if detected_columns is not None else _empty_columns(),
        "total_rows": total_rows,
        "status_distribution": status_distribution if status_distribution is not None else {},
        "attempt_distribution": attempt_distribution if attempt_distribution is not None else {},
        "claim_owner_counts": claim_owner_counts if claim_owner_counts is not None else {},
        "pass": passed,
    }


def _emit_json(payload: dict[str, object]) -> None:
    """Emit exactly one compact, key-sorted JSON object (no payload bodies)."""
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _audit(db_path: str, table: str, json_mode: bool = False) -> int:
    resolved = Path(db_path).expanduser().resolve()
    uri = resolved.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        query_only = conn.execute("PRAGMA query_only").fetchone()[0]

        stat = os.stat(resolved)
        mtime = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")

        if not json_mode:
            print(f"Database: {resolved}")
            print(f"Open mode: mode=ro, query_only={query_only} (ON=1)")
            print(f"File: mtime={mtime} size_bytes={stat.st_size}")

        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
        ).fetchone()
        if found is None:
            if json_mode:
                _emit_json(_result(str(resolved)))
            else:
                print(f"error: expected table {table!r} is absent", file=sys.stderr)
            return 1

        quoted = _quote(table)
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")]

        status_cols = [c for c in columns if c in STATUS_COLUMNS]
        attempt_cols = [c for c in columns if c in ATTEMPT_COLUMNS]
        owner_cols = [c for c in columns if c in OWNER_COLUMNS]
        timestamp_cols = [c for c in columns if _is_timestamp(c)]

        if not json_mode:
            print("Detected columns:")
            print(f"  status: {', '.join(status_cols) if status_cols else 'absent'}")
            print(f"  attempt_count: {', '.join(attempt_cols) if attempt_cols else 'absent'}")
            print(f"  claim_owner: {', '.join(owner_cols) if owner_cols else 'absent'}")
            print(f"  timestamps: {', '.join(timestamp_cols) if timestamp_cols else 'absent'}")

        total = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        if not json_mode:
            print(f"Total rows: {total}")

        status_distribution: dict[str, int] = {}
        if status_cols:
            col = _quote(status_cols[0])
            rows = conn.execute(
                f"SELECT {col}, COUNT(*) FROM {quoted} GROUP BY {col} ORDER BY COUNT(*) DESC, {col}"
            ).fetchall()
            if not json_mode:
                print("Status distribution:")
            for value, count in rows:
                label = _safe_label(value)
                status_distribution[label] = status_distribution.get(label, 0) + count
                if not json_mode:
                    print(f"  {label}: {count}")
        elif not json_mode:
            print("Status distribution: unavailable (column absent)")

        attempt_distribution: dict[str, int] = {}
        if attempt_cols:
            col = _quote(attempt_cols[0])
            rows = conn.execute(f"SELECT {col}, COUNT(*) FROM {quoted} GROUP BY {col} ORDER BY {col}").fetchall()
            if not json_mode:
                print("Attempt distribution:")
            for value, count in rows:
                label = _safe_label(value)
                attempt_distribution[label] = attempt_distribution.get(label, 0) + count
                if not json_mode:
                    print(f"  {label}: {count}")
        elif not json_mode:
            print("Attempt distribution: unavailable (column absent)")

        claim_owner_counts: dict[str, int] = {}
        if owner_cols:
            col = _quote(owner_cols[0])
            rows = conn.execute(
                f"SELECT {col}, COUNT(*) FROM {quoted} GROUP BY {col} ORDER BY COUNT(*) DESC"
            ).fetchall()
            quarantine_total = 0
            if not json_mode:
                print("Claim-owner counts (non-quarantine owners redacted):")
            for value, count in rows:
                label = _owner_label(value)
                claim_owner_counts[label] = claim_owner_counts.get(label, 0) + count
                if not json_mode:
                    print(f"  {label}: {count}")
                if value is not None and str(value).lower().startswith(_QUARANTINE_PREFIX):
                    quarantine_total += count
            if not json_mode:
                print(f"Quarantine markers total: {quarantine_total}")
        elif not json_mode:
            print("Claim-owner counts: unavailable (column absent)")

        if timestamp_cols:
            if not json_mode:
                print("Timestamp ranges:")
            for column in timestamp_cols:
                col = _quote(column)
                low, high = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {quoted}").fetchone()
                if not json_mode:
                    print(f"  {column}: min={_safe_label(low)} max={_safe_label(high)}")

        if json_mode:
            _emit_json(
                _result(
                    str(resolved),
                    size_bytes=stat.st_size,
                    mtime=mtime,
                    detected_columns={
                        "status": sorted(status_cols),
                        "attempt_count": sorted(attempt_cols),
                        "claim_owner": sorted(owner_cols),
                        "timestamps": sorted(timestamp_cols),
                    },
                    total_rows=total,
                    status_distribution=status_distribution,
                    attempt_distribution=attempt_distribution,
                    claim_owner_counts=claim_owner_counts,
                    passed=True,
                )
            )

        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only, schema-aware outbox audit (never writes, claims or prints payloads)."
    )
    parser.add_argument("db_path", help="path to the outbox SQLite database")
    parser.add_argument("--table", default=DEFAULT_TABLE, help=f"outbox table name (default: {DEFAULT_TABLE})")
    parser.add_argument("--json", action="store_true", help="emit one key-sorted JSON object instead of human output")
    args = parser.parse_args(argv)

    if not _IDENTIFIER.fullmatch(args.table):
        print("error: --table must be a plain SQL identifier", file=sys.stderr)
        return 2

    try:
        return _audit(args.db_path, args.table, args.json)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"error: cannot audit database: {exc}", file=sys.stderr)
        if args.json:
            resolved = Path(args.db_path).expanduser().resolve()
            _emit_json(_result(str(resolved)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
