"""Single-writer fencing-term RESEARCH authority, NOT quorum or failover.

Every mutation uses SQLite BEGIN IMMEDIATE and an independently located
local checkpoint. The checkpoint advances BEFORE DB commit; a crash between
those operations permanently FAILS CLOSED rather than reusing old state.
It detects DB-only rollback when checkpoint survives, NOT coordinated
rollback/cloning of both files, malicious host operators or split-brain.
No production route imports this module.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from contextlib import closing
from typing import Callable, TypeVar

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

T = TypeVar("T")
_TX = re.compile(r"^neo4j-transaction-[0-9]+$")
_RECEIPT = "assistx-fencing-closure-research-v1"


class FenceDenied(RuntimeError):
    pass


def _canon(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def _connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA journal_mode=DELETE")
    return db


def _rows(db: sqlite3.Connection) -> list[dict]:
    return [dict(zip(("id", "owner", "term", "operation", "state",
                      "server", "generation", "txid"), row))
            for row in db.execute(
                "SELECT id,owner,term,operation,state,server,generation,txid "
                "FROM attempts ORDER BY id")]


def _state(db: sqlite3.Connection) -> dict:
    row = db.execute(
        "SELECT instance,revision,term,owner,capacity,witness_public FROM authority"
    ).fetchone()
    if row is None:
        raise FenceDenied("AUTHORITY_MISSING")
    return dict(zip(("instance", "revision", "term", "owner", "capacity",
                     "witness_public"), row))


def _checkpoint(db: sqlite3.Connection) -> dict:
    state = _state(db)
    digest = hashlib.sha256(_canon({"state": state, "attempts": _rows(db)})).hexdigest()
    return {"instance": state["instance"], "revision": state["revision"],
            "term": state["term"], "digest": digest}


def _read_checkpoint(path: Path) -> dict:
    try:
        checkpoint = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise FenceDenied("ANCHOR_UNAVAILABLE") from exc
    if (not isinstance(checkpoint, dict) or
            set(checkpoint) != {"instance", "revision", "term", "digest"}):
        raise FenceDenied("ANCHOR_INVALID")
    return checkpoint


def _store_checkpoint(path: Path, data: dict) -> None:
    # RESEARCH local two-file guard only; not independent quorum custody.
    # Never recover automatically after a write/commit mismatch.
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".pending")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(_canon(data))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        temp.unlink(missing_ok=True)


class ResearchFencingAuthority:
    """Conservative bounded authority; only signed witness can close.

    Gateways may mark tx metadata, but never call release without detached
    witness signature. Unresolved RESERVED/STARTED/UNCERTAIN blocks takeover.
    """

    def __init__(self, db_path: str | Path, checkpoint_path: str | Path):
        self.db_path = Path(db_path)
        self.checkpoint_path = Path(checkpoint_path)
        if self.db_path.resolve() == self.checkpoint_path.resolve():
            raise ValueError("DB_AND_CHECKPOINT_MUST_DIFFER")
        if not self.db_path.is_file():
            raise FenceDenied("NO_EXISTING_AUTHORITY")
        self.snapshot()  # fail-closed on initial mismatch

    @classmethod
    def bootstrap(cls, db_path: str | Path, checkpoint_path: str | Path,
                  owner: str, capacity: int, witness_public: bytes):
        db_path, checkpoint_path = Path(db_path), Path(checkpoint_path)
        if (db_path.exists() or checkpoint_path.exists() or
                db_path.resolve() == checkpoint_path.resolve()):
            raise FenceDenied("BOOTSTRAP_REQUIRES_FRESH_STORAGE")
        if not owner or not isinstance(capacity, int) or type(capacity) is not int \
                or not 1 <= capacity <= 16 or len(witness_public) != 32:
            raise FenceDenied("INVALID_BOOTSTRAP")
        try:
            with closing(_connect(db_path)) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE authority (instance TEXT NOT NULL PRIMARY KEY,"
                    "revision INTEGER NOT NULL, term INTEGER NOT NULL,"
                    "owner TEXT NOT NULL, capacity INTEGER NOT NULL,"
                    "witness_public TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE attempts (id TEXT PRIMARY KEY,"
                    "owner TEXT NOT NULL, term INTEGER NOT NULL,"
                    "operation TEXT NOT NULL UNIQUE, state TEXT NOT NULL,"
                    "server TEXT, generation TEXT, txid TEXT)"
                )
                db.execute(
                    "CREATE TABLE used_receipts (hash TEXT PRIMARY KEY)"
                )
                db.execute("INSERT INTO authority VALUES(?,?,?,?,?,?)",
                           (secrets.token_hex(16), 1, 1, owner, capacity,
                            witness_public.hex()))
                anchor = _checkpoint(db)
                _store_checkpoint(checkpoint_path, anchor)
                db.execute("COMMIT")
        except Exception:
            # Do not silently reinitialize when a partial genesis exists.
            raise
        return cls(db_path, checkpoint_path)

    def _transaction(self, fn: Callable[[sqlite3.Connection, dict], T],
                     mutate: bool = True) -> T:
        with closing(_connect(self.db_path)) as db:
            db.execute("BEGIN IMMEDIATE")
            before = _checkpoint(db)
            if before != _read_checkpoint(self.checkpoint_path):
                raise FenceDenied("FENCING_CHECKPOINT_MISMATCH")
            state = _state(db)
            result = fn(db, state)
            if mutate:
                db.execute("UPDATE authority SET revision=revision+1")
                _store_checkpoint(self.checkpoint_path, _checkpoint(db))
            db.execute("COMMIT")
            return result

    def snapshot(self) -> dict:
        def read(db, state):
            pending = sum(r["state"] != "CLOSED" for r in _rows(db))
            return {"term": state["term"], "owner": state["owner"],
                    "revision": state["revision"], "pending": pending,
                    "instance": state["instance"]}
        return self._transaction(read, mutate=False)

    def admit(self, owner: str, term: int, operation: str) -> str:
        if not isinstance(operation, str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,128}", operation):
            raise FenceDenied("INVALID_OPERATION")
        def action(db, state):
            if (owner != state["owner"] or type(term) is not int
                    or term != state["term"]):
                raise FenceDenied("STALE_FENCING_TERM")
            used = db.execute(
                "SELECT count(*) FROM attempts WHERE state != 'CLOSED'"
            ).fetchone()[0]
            if used >= state["capacity"]:
                raise FenceDenied("PENDING_PHYSICAL_CAPACITY")
            attempt_id = secrets.token_hex(16)
            try:
                db.execute(
                    "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)",
                    (attempt_id, owner, term, operation, "RESERVED", None, None, None)
                )
            except sqlite3.IntegrityError as exc:
                raise FenceDenied("DUPLICATE_OPERATION") from exc
            return attempt_id
        return self._transaction(action)

    def bind(self, owner: str, term: int, attempt_id: str,
             server: str, generation: str, txid: str):
        if (not server or not generation or not isinstance(txid, str)
                or not _TX.fullmatch(txid)):
            raise FenceDenied("INVALID_SERVER_TRANSACTION")
        def action(db, state):
            if owner != state["owner"] or type(term) is not int \
                    or term != state["term"]:
                raise FenceDenied("STALE_FENCING_TERM")
            row = db.execute(
                "SELECT owner,term,state FROM attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if row != (owner, term, "RESERVED"):
                raise FenceDenied("ATTEMPT_NOT_RESERVABLE")
            db.execute(
                "UPDATE attempts SET state='STARTED',server=?,generation=?,txid=?"
                " WHERE id=?", (server, generation, txid, attempt_id)
            )
        self._transaction(action)

    def quarantine(self, attempt_id: str):
        def action(db, _):
            row = db.execute(
                "SELECT state FROM attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if not row or row[0] == "CLOSED":
                raise FenceDenied("UNKNOWN_OR_CLOSED_ATTEMPT")
            db.execute(
                "UPDATE attempts SET state='UNCERTAIN' WHERE id=?", (attempt_id,)
            )
        self._transaction(action)

    def close(self, receipt: dict, signature: bytes) -> None:
        """Cryptographic admission control ONLY; physical witness is external."""
        if not isinstance(receipt, dict) or set(receipt) != {
                "schema", "instance", "term", "owner", "attempt_id",
                "server", "generation", "txid", "observation"}:
            raise FenceDenied("INVALID_WITNESS_RECEIPT")
        if (receipt["schema"] != _RECEIPT or
                receipt["observation"] != "two-absent-snapshots"):
            raise FenceDenied("INVALID_WITNESS_OBSERVATION")
        if not isinstance(signature, bytes):
            raise FenceDenied("INVALID_WITNESS_SIGNATURE")
        def action(db, state):
            from cryptography.exceptions import InvalidSignature
            try:
                Ed25519PublicKey.from_public_bytes(
                    bytes.fromhex(state["witness_public"])
                ).verify(signature, _canon(receipt))
            except (ValueError, InvalidSignature) as exc:
                raise FenceDenied("INVALID_WITNESS_SIGNATURE") from exc
            row = db.execute(
                "SELECT owner,term,state,server,generation,txid "
                "FROM attempts WHERE id=?", (receipt["attempt_id"],)
            ).fetchone()
            if (row is None or row[2] not in ("STARTED", "UNCERTAIN") or
                    row[0] != receipt["owner"] or row[1] != receipt["term"] or
                    row[3:] != (receipt["server"], receipt["generation"],
                                 receipt["txid"]) or
                    receipt["instance"] != state["instance"]):
                raise FenceDenied("WITNESS_ATTEMPT_MISMATCH")
            digest = hashlib.sha256(_canon(receipt) + signature).hexdigest()
            if db.execute(
                "SELECT 1 FROM used_receipts WHERE hash=?", (digest,)
            ).fetchone():
                raise FenceDenied("WITNESS_REPLAY")
            db.execute("INSERT INTO used_receipts(hash) VALUES(?)", (digest,))
            db.execute(
                "UPDATE attempts SET state='CLOSED' WHERE id=?",
                (receipt["attempt_id"],)
            )
        self._transaction(action)

    def takeover(self, new_owner: str, expected_term: int) -> int:
        if not new_owner or not isinstance(new_owner, str):
            raise FenceDenied("INVALID_OWNER")
        def action(db, state):
            if expected_term != state["term"] or type(expected_term) is not int:
                raise FenceDenied("STALE_FENCING_TERM")
            if new_owner == state["owner"]:
                raise FenceDenied("SAME_OWNER")
            pending = db.execute(
                "SELECT count(*) FROM attempts WHERE state!='CLOSED'"
            ).fetchone()[0]
            if pending:
                raise FenceDenied("UNCERTAIN_INFLIGHT_TAKEOVER_BLOCKED")
            term = state["term"] + 1
            db.execute("UPDATE authority SET term=?,owner=?", (term, new_owner))
            return term
        return self._transaction(action)


def make_receipt(snapshot: dict, owner: str, term: int, attempt_id: str,
                 server: str, generation: str, txid: str) -> dict:
    """Creates an UNTRUSTED receipt body for an independent witness to sign.

    The caller must NOT sign until actual physical closure is verified.
    """
    return {
        "schema": _RECEIPT, "instance": snapshot["instance"],
        "owner": owner, "term": term, "attempt_id": attempt_id,
        "server": server, "generation": generation, "txid": txid,
        "observation": "two-absent-snapshots",
    }
