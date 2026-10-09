"""Disposable xwing-only single witness: a fenced *synthetic* effect gate.

Every real effect would require its own atomic, resource-enforced fence.
There is no consensus, failover, distributed quorum, lease expiry, or
production execution permission in this experiment. Never copy/promote DB.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat
import uuid
from urllib.parse import quote

SCHEMA = "assistx-single-independent-witness-research-v1"
FILE = "witness-test.sqlite"


@dataclass(frozen=True)
class Decision:
    status: str
    term: int | None = None
    sequence: int | None = None
    active: int | None = None
    token: str | None = None


def _path(value: str) -> Path:
    p = Path(value)
    if (not p.is_absolute() or p.parent.parent != Path("/tmp")
        or not p.parent.name.startswith("assistx-twohost-witness-research-")
        or p.name != FILE or p.is_symlink() or p.parent.is_symlink()):
        raise ValueError("DISPOSABLE_WITNESS_PATH_REQUIRED")
    return p


def _connect(p: Path):
    conn = sqlite3.connect("file:" + quote(str(p), safe="/") + "?mode=rw",
                           uri=True, isolation_level=None, timeout=1.0)
    conn.execute("PRAGMA busy_timeout=1000")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def bootstrap(path: str, *, epoch: str, graph: str, holder: str = "x1-370"):
    p = _path(path)
    if (not isinstance(epoch, str) or str(uuid.UUID(epoch)) != epoch
        or uuid.UUID(epoch).version != 4
        or type(graph) is not str or len(graph) != 64
        or any(c not in "0123456789abcdef" for c in graph)
        or holder not in ("x1-370", "xwing") or p.exists()):
        raise ValueError("INVALID_WITNESS_BOOTSTRAP")
    p.parent.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with _connect(p) as con:
        con.executescript("""
          CREATE TABLE head(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            schema TEXT NOT NULL, epoch TEXT NOT NULL, graph TEXT NOT NULL,
            holder TEXT NOT NULL, term INTEGER NOT NULL, seq INTEGER NOT NULL);
          CREATE TABLE grants(
            token TEXT PRIMARY KEY, ref TEXT UNIQUE NOT NULL,
            holder TEXT NOT NULL, term INTEGER NOT NULL,
            sequence INTEGER NOT NULL UNIQUE, active INTEGER NOT NULL);
          CREATE TABLE effects(
            effect_ref TEXT PRIMARY KEY, token TEXT NOT NULL,
            holder TEXT NOT NULL, term INTEGER NOT NULL);
        """)
        con.execute("INSERT INTO head VALUES(1,?,?,?,?,1,0)",
                    (SCHEMA, epoch, graph, holder))


class Witness:
    """Single independent serialized witness, never a copied HA pair."""

    def __init__(self, path: str, *, epoch: str, graph: str, floor: int = 1):
        self.path = _path(path)
        if (type(floor) is not int or floor < 1
            or not isinstance(epoch, str) or not isinstance(graph, str)):
            raise ValueError("EXTERNALLY_PINNED_WITNESS_REQUIRED")
        self.epoch, self.graph, self.floor = epoch, graph, floor
        self.identity = self._file_id()
        with _connect(self.path) as con:
            self._validate(con)

    def _file_id(self):
        st = self.path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
            or st.st_mode & 0o077):
            raise ValueError("UNSAFE_WITNESS_FILE")
        return st.st_dev, st.st_ino

    def _validate(self, con):
        if self._file_id() != self.identity:
            raise ValueError("WITNESS_FILE_REPLACED")
        row = con.execute("SELECT schema,epoch,graph,holder,term,seq FROM head"
                          " WHERE singleton=1").fetchone()
        if (not row or row[:3] != (SCHEMA, self.epoch, self.graph)
            or row[3] not in ("x1-370", "xwing")
            or type(row[4]) is not int or row[4] < self.floor
            or type(row[5]) is not int or row[5] < 0
            or con.execute("SELECT COUNT(*) FROM head").fetchone()[0] != 1):
            raise ValueError("WITNESS_IDENTITY_OR_TERM_ROLLBACK")
        return row[3], row[4], row[5]

    def snapshot(self):
        with _connect(self.path) as con:
            holder, term, seq = self._validate(con)
            active = con.execute("SELECT count(*) FROM grants"
                                 " WHERE active=1").fetchone()[0]
            return {"schema": SCHEMA, "epoch": self.epoch, "graph": self.graph,
                    "holder": holder, "term": term, "sequence": seq,
                    "active": active, "capacity": 1}

    def _transaction(self, operation):
        try:
            with _connect(self.path) as con:
                con.execute("BEGIN IMMEDIATE")
                holder, term, seq = self._validate(con)
                decision = operation(con, holder, term, seq)
                if self._file_id() != self.identity:
                    con.rollback()
                    return Decision("witness-identity-uncertain")
                con.commit()
                return decision
        except (OSError, sqlite3.Error, ValueError):
            return Decision("witness-unavailable")

    def admit(self, ref: str, holder: str, term: int):
        if (type(ref) is not str or not ref.startswith("synthetic-")
            or not 1 <= len(ref) <= 120
            or any(not (c.isascii() and (c.isalnum() or c in "-_:."))
                   for c in ref)):
            return Decision("invalid-ref")
        def run(con, current, generation, seq):
            if type(term) is not int or term != generation:
                return Decision("stale-term", generation, seq)
            if holder != current:
                return Decision("wrong-holder", generation, seq)
            if con.execute("SELECT 1 FROM grants WHERE ref=?", (ref,)).fetchone():
                return Decision("duplicate-request", generation, seq)
            active = con.execute("SELECT count(*) FROM grants"
                                 " WHERE active=1").fetchone()[0]
            if active:
                return Decision("capacity-full", generation, seq, active)
            token = uuid.uuid4().hex
            con.execute("INSERT INTO grants VALUES(?,?,?,?,?,1)",
                        (token, ref, holder, term, seq + 1))
            con.execute("UPDATE head SET seq=? WHERE singleton=1", (seq + 1,))
            return Decision("admitted", generation, seq + 1, 1, token)
        return self._transaction(run)

    def synthetic_effect(self, token: str, holder: str, term: int,
                         effect_ref: str):
        if (type(effect_ref) is not str or not effect_ref.startswith("synthetic-")
            or len(effect_ref) > 120):
            return Decision("invalid-effect-ref")
        def run(con, current, generation, seq):
            if type(term) is not int or term != generation or holder != current:
                return Decision("stale-effect", generation, seq)
            grant = con.execute("SELECT holder,term,active FROM grants"
                                " WHERE token=?", (token,)).fetchone()
            if grant != (holder, term, 1):
                return Decision("unadmitted-effect", generation, seq)
            if con.execute("SELECT 1 FROM effects WHERE effect_ref=?",
                           (effect_ref,)).fetchone():
                return Decision("duplicate-effect", generation, seq)
            con.execute("INSERT INTO effects VALUES(?,?,?,?)",
                        (effect_ref, token, holder, term))
            return Decision("applied-synthetic", generation, seq)
        return self._transaction(run)

    def complete(self, token: str, holder: str, term: int):
        def run(con, current, generation, seq):
            if type(term) is not int or term != generation or holder != current:
                return Decision("stale-completion", generation, seq)
            n = con.execute("UPDATE grants SET active=0 WHERE token=?"
                            " AND holder=? AND term=? AND active=1",
                            (token, holder, term)).rowcount
            return Decision("completed" if n == 1 else "unknown-grant",
                            generation, seq)
        return self._transaction(run)

    def rotate(self, next_holder: str, expected_term: int):
        def run(con, holder, generation, seq):
            if (type(expected_term) is not int or generation != expected_term
                or next_holder not in ("x1-370", "xwing")
                or next_holder == holder):
                return Decision("rotation-denied", generation, seq)
            if con.execute("SELECT 1 FROM grants WHERE active=1"
                           " LIMIT 1").fetchone():
                return Decision("reconciliation-required", generation, seq, 1)
            con.execute("UPDATE head SET holder=?,term=? WHERE singleton=1",
                        (next_holder, generation + 1))
            return Decision("rotated", generation + 1, seq, 0)
        return self._transaction(run)
