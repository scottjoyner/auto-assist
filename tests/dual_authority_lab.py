"""Disposable two-ledger antirollback / uncertain-commit research.

Two separate SQLite files, but ONE host, ONE operator, and NO independent
administrative custody. No Neo4j, network, production data or credentials.
All observer results HOLD; neither admission nor readback authorizes effects.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,95}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class Decision:
    state: str
    decision: str = "HOLD"
    may_ack: bool = False
    may_retry_effect: bool = False
    may_promote: bool = False


def _valid(key: str, digest: str) -> bool:
    return isinstance(key, str) and isinstance(digest, str) and bool(
        _KEY.fullmatch(key) and _HASH.fullmatch(digest)
    )


def _db(path: Path):
    # read/write only for explicit fixture setup or controlled simulation
    con = sqlite3.connect(str(path), timeout=12, isolation_level=None)
    con.execute("PRAGMA busy_timeout=12000")
    return con


def _read_db(path: Path):
    # Never create missing evidence on a "read": absence must be observable.
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)


class Authority:
    """Simulated spent-operation guardian. NOT an immutable remote witness."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def bootstrap(self):
        with _db(self.path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS term_head(
                  scope TEXT PRIMARY KEY, term INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS claims(
                  op_key TEXT PRIMARY KEY, digest TEXT NOT NULL,
                  term INTEGER NOT NULL,
                  state TEXT NOT NULL CHECK(state IN ('PREPARED','OBSERVED')));
            """)
            db.execute("INSERT OR IGNORE INTO term_head VALUES('context',1)")

    def reserve(self, key: str, digest: str, *, term: int = 1) -> str:
        if not _valid(key, digest) or type(term) is not int or term < 1:
            return "INVALID"
        with _db(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            head = db.execute("SELECT term FROM term_head WHERE scope='context'").fetchone()
            if head is None or head[0] != term:
                db.rollback()
                return "FENCED"
            previous = db.execute(
                "SELECT digest,state FROM claims WHERE op_key=?", (key,)
            ).fetchone()
            if previous:
                db.rollback()
                if previous[0] != digest:
                    return "CONFLICT"
                return previous[1] + "_EXISTS"
            db.execute("INSERT INTO claims VALUES(?,?,?,?)",
                       (key, digest, term, "PREPARED"))
            db.commit()
            return "PREPARED_NEW"

    def observe(self, key: str, digest: str, resource: "Resource") -> str:
        # Research only: a local resource readback is not a signed COMMIT proof.
        if not _valid(key, digest):
            return "INVALID"
        try:
            resource_row = resource.read(key)
        except (sqlite3.Error, OSError):
            return "UNAVAILABLE"
        if resource_row != digest:
            return "RESOURCE_NOT_VERIFIED"
        with _db(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT digest,state FROM claims WHERE op_key=?", (key,)
            ).fetchone()
            if not row or row[0] != digest:
                db.rollback()
                return "CONFLICT_OR_MISSING"
            db.execute("UPDATE claims SET state='OBSERVED' WHERE op_key=?", (key,))
            db.commit()
        return "OBSERVED_UNATTESTED"

    def read(self, key: str):
        with _read_db(self.path) as db:
            return db.execute(
                "SELECT digest,state FROM claims WHERE op_key=?", (key,)
            ).fetchone()

    def head(self):
        with _read_db(self.path) as db:
            return db.execute("SELECT term FROM term_head WHERE scope='context'").fetchone()[0]


class Resource:
    """Simulated effect resource with no true issuer/write-principal isolation."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def bootstrap(self):
        with _db(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS effects(
                op_key TEXT PRIMARY KEY, digest TEXT NOT NULL)""")

    def apply_direct(self, key: str, digest: str) -> str:
        """Dangerous direct writer BYPASS control intentionally retained."""
        if not _valid(key, digest):
            return "INVALID"
        with _db(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT digest FROM effects WHERE op_key=?", (key,)
            ).fetchone()
            if previous is not None:
                db.rollback()
                return "DUPLICATE" if previous[0] == digest else "CONFLICT"
            db.execute("INSERT INTO effects VALUES(?,?)", (key, digest))
            db.commit()
            return "EFFECT_WRITTEN"

    def read(self, key: str):
        with _read_db(self.path) as db:
            row = db.execute("SELECT digest FROM effects WHERE op_key=?", (key,)).fetchone()
        return row[0] if row else None


def reconcile(authority: Authority, resource: Resource, key: str,
              digest: str, term: int = 1) -> Decision:
    if not _valid(key, digest):
        return Decision("BAD_OPERATION_IDENTITY")
    if type(term) is not int or term < 1:
        return Decision("BAD_TERM")
    try:
        head = authority.head()
        claim = authority.read(key)
    except (OSError, sqlite3.Error):
        return Decision("AUTHORITY_UNAVAILABLE")
    try:
        effect = resource.read(key)
    except (OSError, sqlite3.Error):
        return Decision("RESOURCE_UNAVAILABLE")
    if head != term:
        return Decision("TERM_FENCED")
    if claim and claim[0] != digest:
        return Decision("CLAIM_DIGEST_CONFLICT")
    if effect is not None and effect != digest:
        return Decision("RESOURCE_DIGEST_CONFLICT")
    if not claim:
        return Decision("ORPHAN_EFFECT" if effect else "UNISSUED_UNPROVEN")
    if claim[1] == "PREPARED":
        return Decision("EFFECT_UNACKED" if effect else "PREPARED_UNCERTAIN")
    if claim[1] == "OBSERVED":
        return Decision("CONSISTENT_UNATTESTED" if effect else "RESOURCE_ROLLBACK_SUSPECTED")
    return Decision("UNKNOWN_AUTHORITY_STATE")
