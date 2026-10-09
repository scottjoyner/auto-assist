"""RESEARCH ONLY: single-host durable, non-expiring physical-read reservations.

Not imported by runtime routes. SQLite file must be on a *local* filesystem;
there is no cross-host locking/replication, signed Neo4j termination witness,
external epoch authority, rollback defense, or production admission guarantee.
The research helper can only create uniquely named disposable /tmp fixtures.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = "trace-physical-ledger-research-v1"
RECEIPT_VERSION = "trace-query-closure-research-v1"
CLOSED = "remote_query_terminated"
MAX_SLOTS = 16


@dataclass(frozen=True)
class Decision:
    token: str | None
    active: int | None
    reason: str


def _epoch(value: str) -> bool:
    try:
        return type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4
    except (TypeError, ValueError, AttributeError):
        return False


def _query_ref(value: str) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and all(c.isascii() and (c.isalnum() or c in "-_:.") for c in value)
    )


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=1, isolation_level=None)
    conn.execute("PRAGMA busy_timeout=1000")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def bootstrap_disposable_fixture(path: str, epoch: str, capacity: int) -> None:
    """Explicit research-only creation. NEVER called by an admission handler.

    Only creates a new file below an exact /tmp/assistx-trace-ledger-test-*
    directory. It never reinitializes or repairs a missing/failed live ledger.
    """
    p = Path(path)
    root = Path("/tmp")
    if (
        not p.is_absolute()
        or p.name != "trace-ledger-test.sqlite"
        or not p.parent.name.startswith("assistx-trace-ledger-test-")
        or p.parent.parent != root
        or p.parent.is_symlink()
        or not _epoch(epoch)
        or type(capacity) is not int
        or not 1 <= capacity <= MAX_SLOTS
        or p.exists() or p.is_symlink()
    ):
        raise ValueError("RESEARCH_BOOTSTRAP_REFUSED")
    os.mkdir(p.parent, 0o700) if not p.parent.exists() else None
    # Fail closed if another process has created the target since p.exists().
    fd = os.open(str(p), os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    try:
        with _connect(p) as conn:
            conn.executescript("""
              CREATE TABLE ledger_meta (epoch TEXT NOT NULL PRIMARY KEY,
                                        schema_version TEXT NOT NULL, capacity INTEGER NOT NULL);
              CREATE TABLE reservations (
                token TEXT NOT NULL PRIMARY KEY, epoch TEXT NOT NULL,
                query_ref TEXT NOT NULL, created_ms INTEGER NOT NULL,
                UNIQUE(epoch, query_ref));
            """)
            conn.execute("INSERT INTO ledger_meta VALUES (?, ?, ?)", (epoch, SCHEMA_VERSION, capacity))
    except Exception:
        # Deliberately DO NOT auto-remove a damaged fixture and silently rearm.
        raise


class DurableTraceReadLedger:
    """Durable *local* pessimistic admission; never infers remote completion.

    The independent caller pins the epoch and public verification key.
    No expiry or worker-death reclamation; crash strands reservations.
    A signed research closure receipt is needed to release a slot.
    """

    def __init__(self, path: str, expected_epoch: str, verifier_public_key: bytes):
        p = Path(path)
        if not p.is_absolute() or not _epoch(expected_epoch):
            raise ValueError("EXPLICIT_PINNED_EPOCH_AND_ABSOLUTE_PATH_REQUIRED")
        if type(verifier_public_key) is not bytes or len(verifier_public_key) != 32:
            raise ValueError("INDEPENDENT_PUBLIC_KEY_REQUIRED")
        self.path = p
        self.epoch = expected_epoch
        self.verifier = Ed25519PublicKey.from_public_bytes(verifier_public_key)
        # Never create or repair the ledger on runtime startup.
        self._identity = self._identity_from_disk()
        with _connect(p) as conn:
            self._validate(conn)

    def _identity_from_disk(self) -> tuple[int, int]:
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("UNSAFE_LEDGER_FILE")
        return info.st_dev, info.st_ino

    def _validate(self, conn: sqlite3.Connection) -> int:
        if self._identity_from_disk() != self._identity:
            raise ValueError("LEDGER_FILE_IDENTITY_CHANGED")
        row = conn.execute(
            "SELECT schema_version, capacity FROM ledger_meta WHERE epoch = ?", (self.epoch,)
        ).fetchone()
        if not row or row[0] != SCHEMA_VERSION or type(row[1]) is not int or not 1 <= row[1] <= MAX_SLOTS:
            raise ValueError("LEDGER_EPOCH_OR_SCHEMA_NOT_PROVEN")
        # No permissive 'if missing, create new ledger' anywhere in this path.
        return row[1]

    def acquire(self, query_ref: str) -> Decision:
        if not _query_ref(query_ref):
            return Decision(None, None, "invalid-query-ref")
        token = uuid.uuid4().hex
        try:
            with _connect(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                slots = self._validate(conn)
                active = conn.execute("SELECT count(*) FROM reservations").fetchone()[0]
                if active >= slots:
                    conn.rollback()
                    return Decision(None, active, "full")
                if conn.execute(
                    "SELECT 1 FROM reservations WHERE epoch=? AND query_ref=?",
                    (self.epoch, query_ref),
                ).fetchone():
                    conn.rollback()
                    return Decision(None, active, "duplicate-query-ref")
                conn.execute(
                    "INSERT INTO reservations VALUES (?, ?, ?, ?)",
                    (token, self.epoch, query_ref, time.time_ns() // 1_000_000),
                )
                conn.commit()
                if self._identity_from_disk() != self._identity:
                    return Decision(None, None, "ledger-identity-uncertain")
                return Decision(token, active + 1, "admitted")
        except Exception as exc:
            LOGGER.warning("Research trace admission denied: %s", type(exc).__name__)
            return Decision(None, None, "unavailable")

    def acknowledge_remote_closure(self, receipt: dict, signature: bytes) -> bool:
        """Research verifier signature is NOT verified Neo4j cancellation.

        Only a completely separate verifier with a private key can mint a
        receipt. Callers must not control that key or infer closure from a
        timeout/exception/HTTP status. Invalid/mismatched/replayed receipts
        leave the reservation intact.
        """
        required = {"version", "epoch", "token", "query_ref", "evidence_id", "verdict"}
        if (
            type(receipt) is not dict
            or set(receipt) != required
            or type(signature) is not bytes
            or len(signature) != 64
            or receipt["version"] != RECEIPT_VERSION
            or receipt["epoch"] != self.epoch
            or receipt["verdict"] != CLOSED
            or type(receipt["token"]) is not str
            or len(receipt["token"]) != 32
            or any(c not in "0123456789abcdef" for c in receipt["token"])
            or not _query_ref(receipt["query_ref"])
            or not _query_ref(receipt["evidence_id"])
        ):
            return False
        try:
            self.verifier.verify(signature, _canonical(receipt))
            with _connect(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._validate(conn)
                removed = conn.execute(
                    "DELETE FROM reservations WHERE token=? AND epoch=? AND query_ref=?",
                    (receipt["token"], self.epoch, receipt["query_ref"]),
                ).rowcount
                conn.commit()
                if self._identity_from_disk() != self._identity:
                    return False
                return removed == 1
        except (InvalidSignature, Exception) as exc:
            LOGGER.warning("Research trace closure denied: %s", type(exc).__name__)
            return False

    def inspect(self) -> int | None:
        try:
            with _connect(self.path) as conn:
                self._validate(conn)
                return conn.execute("SELECT count(*) FROM reservations").fetchone()[0]
        except Exception:
            return None
