"""SQLite research journal: crash-durable local hash chain, NOT trusted custody.

Only disposal/test fixtures. A DB operator can rewrite both rows and hashes;
production needs independent append-only storage, signed checkpoints and keys.
Never persist reservation tokens, raw Cypher or parameters in this ledger.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterator

from trace_graph_entry_research import BoundAttempt

EVENTS = frozenset({
    "ADMISSION_COMMITTED", "GRAPH_ENTRY_INTENT",
    "GRAPH_CALL_RETURNED", "CLOSURE_UNCERTAIN",
})
NEXT = {
    None: frozenset({"ADMISSION_COMMITTED"}),
    "ADMISSION_COMMITTED": frozenset({"GRAPH_ENTRY_INTENT"}),
    "GRAPH_ENTRY_INTENT": frozenset({"GRAPH_CALL_RETURNED", "CLOSURE_UNCERTAIN"}),
    "GRAPH_CALL_RETURNED": frozenset(),
    "CLOSURE_UNCERTAIN": frozenset(),
}
ZERO = "0" * 64


def _encode(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(sequence: int, previous: str, payload: str) -> str:
    return hashlib.sha256(
        f"{sequence}\n{previous}\n{payload}".encode("utf-8")
    ).hexdigest()


class LedgerIntegrityError(RuntimeError):
    pass


class SqliteResearchJournal:
    """Enforce event ordering and durable append-only *application* operations."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        # Do not delete or truncate existing research evidence on reopen.
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS graph_entry_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE,
                UNIQUE(attempt_id,event_type)
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=4, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=4000")
        return db

    def append(self, kind: str, attempt: BoundAttempt, plan_id: str) -> None:
        if kind not in EVENTS or not plan_id or not attempt.attempt_id:
            raise LedgerIntegrityError("INVALID_EVENT")
        # NO token, Cypher, parameters, verifier credentials or signing key.
        payload = _encode({
            "event_type": kind,
            "operation_id": attempt.operation_id,
            "attempt_id": attempt.attempt_id,
            "reservation_id": attempt.grant.reservation_id,
            "epoch": attempt.grant.epoch,
            "term": attempt.grant.term,
            "plan_id": plan_id,
        })
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT event_type,payload FROM graph_entry_events "
                "WHERE attempt_id=? ORDER BY seq DESC LIMIT 1",
                (attempt.attempt_id,),
            ).fetchone()
            previous_kind = row[0] if row else None
            if kind not in NEXT[previous_kind]:
                raise LedgerIntegrityError("INVALID_EVENT_TRANSITION")
            if row:
                prior = json.loads(row[1])
                current = json.loads(payload)
                for field in ("operation_id", "attempt_id", "reservation_id",
                              "epoch", "term", "plan_id"):
                    if prior[field] != current[field]:
                        raise LedgerIntegrityError("EVENT_IDENTITY_CHANGED")
            last = db.execute(
                "SELECT seq,event_hash FROM graph_entry_events "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            sequence = (last[0] if last else 0) + 1
            previous = last[1] if last else ZERO
            db.execute(
                "INSERT INTO graph_entry_events "
                "(seq,attempt_id,event_type,payload,previous_hash,event_hash) "
                "VALUES(?,?,?,?,?,?)",
                (sequence, attempt.attempt_id, kind, payload,
                 previous, _digest(sequence, previous, payload)),
            )
            db.execute("COMMIT")

    def verify_chain(self) -> tuple[int, str]:
        """Validate internal hashes. An external checkpoint must anchor the tip."""
        prior, count = ZERO, 0
        with self._connect() as db:
            rows: Iterator[tuple[int, str, str, str]] = iter(db.execute(
                "SELECT seq,payload,previous_hash,event_hash "
                "FROM graph_entry_events ORDER BY seq"
            ))
            for sequence, payload, previous_hash, event_hash in rows:
                if (sequence != count + 1 or previous_hash != prior
                        or _digest(sequence, prior, payload) != event_hash):
                    raise LedgerIntegrityError("BROKEN_LEDGER_CHAIN")
                prior, count = event_hash, sequence
        return count, prior
