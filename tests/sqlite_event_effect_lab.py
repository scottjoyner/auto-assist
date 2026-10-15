"""Disposable same-database atomic event/effect lab; NEVER production authority.

Standard-library-only, opt-in tests use SQLite files under pytest tmp_path.
It intentionally does not connect to AssistX, Neo4j, Docker or any network.
SQLite semantics here are NOT Neo4j cross-domain commit, owner fencing,
independent signer custody, or rollback-resistant spent-effect authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_KEY = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9:_./-]{0,127}$")


@dataclass(frozen=True)
class Outcome:
    status: str
    effect_id: str | None = None
    may_ack: bool = False
    may_retry: bool = False


class SyntheticInterruption(Exception):
    """Deliberately injected precommit fault in a disposable transaction."""


class SyntheticAckLost(Exception):
    """The transaction committed, but simulated transport lost its ACK."""


def effect_digest(*, actor: str, action: str, target: str,
                  payload: dict[str, Any]) -> str:
    """Pin semantic operation identity, excluding volatile HTTP/event metadata."""
    if not all(isinstance(s, str) and 0 < len(s) <= 128
               for s in (actor, action, target)):
        raise ValueError("invalid semantic identity")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    body = {"version": 1, "actor": actor, "action": action,
            "target": target, "payload": payload}
    try:
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("noncanonical payload") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SyntheticEffectStore:
    """One-owner SQLite control fixture. Not an effect implementation."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path == Path(":memory:") or not self.path.parent.is_dir():
            raise ValueError("fixture requires existing disposable directory")

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=12, isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 12000")
        return connection

    def bootstrap(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS fenced_scope (
                  scope TEXT PRIMARY KEY, term INTEGER NOT NULL CHECK(term >= 1),
                  revision INTEGER NOT NULL CHECK(revision >= 0));
                CREATE TABLE IF NOT EXISTS operations (
                  op_key TEXT PRIMARY KEY, scope TEXT NOT NULL,
                  digest TEXT NOT NULL, effect_id TEXT NOT NULL UNIQUE,
                  term INTEGER NOT NULL, revision INTEGER NOT NULL,
                  status TEXT NOT NULL CHECK(status = 'COMMITTED'));
                CREATE TABLE IF NOT EXISTS effects (
                  effect_id TEXT PRIMARY KEY, op_key TEXT NOT NULL UNIQUE
                    REFERENCES operations(op_key), digest TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO fenced_scope VALUES ('context',1,0)")

    def apply(self, *, key: str, digest: str, term: int = 1,
              expected_revision: int | None = None,
              fault: str | None = None) -> Outcome:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            return Outcome("INVALID_KEY")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return Outcome("INVALID_DIGEST")
        if type(term) is not int or term < 1 or (
            expected_revision is not None and
            (type(expected_revision) is not int or expected_revision < 0)
        ):
            return Outcome("INVALID_FENCE")
        if fault not in (None, "after_reserve", "after_effect", "after_commit_ack_lost"):
            return Outcome("INVALID_INJECTION")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                scope = db.execute(
                    "SELECT term,revision FROM fenced_scope WHERE scope='context'"
                ).fetchone()
                if scope is None or scope[0] != term:
                    db.rollback()
                    return Outcome("FENCED_HOLD")
                row = db.execute(
                    "SELECT scope,digest,effect_id,status FROM operations WHERE op_key=?",
                    (key,)).fetchone()
                if row:
                    if row[0] != "context" or row[1] != digest:
                        db.rollback()
                        return Outcome("CONFLICT")
                    proof = db.execute(
                        "SELECT op_key,digest FROM effects WHERE effect_id=?",
                        (row[2],)).fetchone()
                    if row[3] != "COMMITTED" or proof != (key, digest):
                        db.rollback()
                        return Outcome("UNCERTAIN_HOLD")
                    db.commit()
                    return Outcome("DUPLICATE_COMMITTED", row[2], may_ack=True)
                if expected_revision is not None and scope[1] != expected_revision:
                    db.rollback()
                    return Outcome("STALE_REVISION_HOLD")
                effect_id = hashlib.sha256(("lab-effect:" + key).encode()).hexdigest()
                db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?,?)",
                           (key, "context", digest, effect_id, term,
                            scope[1] + 1, "COMMITTED"))
                if fault == "after_reserve":
                    raise SyntheticInterruption("synthetic reservation before effect")
                db.execute("INSERT INTO effects VALUES (?,?,?)", (effect_id, key, digest))
                if fault == "after_effect":
                    raise SyntheticInterruption("synthetic effect before commit")
                db.execute("UPDATE fenced_scope SET revision=? WHERE scope='context'",
                           (scope[1] + 1,))
                db.commit()
            except Exception:
                db.rollback()
                raise
        if fault == "after_commit_ack_lost":
            raise SyntheticAckLost("synthetic POST response lost after commit")
        return Outcome("COMMITTED", effect_id, may_ack=True)

    def readback(self):
        with self._connect() as db:
            ops = db.execute("SELECT op_key,digest,effect_id FROM operations ORDER BY op_key").fetchall()
            effects = db.execute("SELECT op_key,digest,effect_id FROM effects ORDER BY op_key").fetchall()
            fence = db.execute("SELECT term,revision FROM fenced_scope WHERE scope='context'").fetchone()
        return {"ops": ops, "effects": effects, "term": fence[0], "revision": fence[1]}
