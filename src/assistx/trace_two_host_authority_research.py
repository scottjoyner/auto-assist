"""#148 research: one durable SSH-accessed authority, NOT distributed consensus.

This module NEVER calls Neo4j, Redis, a provider, runtime routing, or socket
APIs. A deliberately disposable SQLite fixture represents one central owner.
Both physical host origins may invoke its CLI over preexisting authenticated
SSH. If the authority is unreachable, clients must refuse; they MUST NOT use
an independent local copy.

The repo also contains a *deliberately failing safety counterexample*:
copying the authority before admission permits two independent clones to
each reserve the same global capacity. Neither local SQLite nor remote SSH
resolves network partitions, consensus, storage rollback, or signer custody.
No release method exists in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat
import uuid
from urllib.parse import quote

SCHEMA="assistx-single-authority-two-host-research-v1"
FILENAME="authority-test.sqlite"


@dataclass(frozen=True)
class Decision:
    status: str
    token: str | None
    receiver_nonce: str | None
    sequence: int | None
    active: int | None


def _uuid4(value: str) -> bool:
    try:
        return type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4
    except (TypeError,ValueError,AttributeError):
        return False


def _path(path: str) -> Path:
    p=Path(path)
    if (not p.is_absolute() or p.name!=FILENAME
        or not p.parent.name.startswith("assistx-twohost-authority-test-")
        or p.parent.parent != Path("/tmp") or p.parent.is_symlink()
        or p.is_symlink()):
        raise ValueError("DISPOSABLE_TWOHOST_AUTHORITY_PATH_REQUIRED")
    return p


def _connect(p: Path):
    conn=sqlite3.connect("file:"+quote(str(p),safe="/")+"?mode=rw",
                         uri=True,isolation_level=None,timeout=1.0)
    conn.execute("PRAGMA busy_timeout=1000")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def bootstrap_research(path: str, *, epoch: str, graph_id: str, capacity: int=1) -> None:
    p=_path(path)
    if (not _uuid4(epoch) or type(graph_id) is not str or len(graph_id)!=64
        or any(x not in "0123456789abcdef" for x in graph_id)
        or type(capacity) is not int or not 1<=capacity<=4 or p.exists()):
        raise ValueError("INVALID_DISPOSABLE_BOOTSTRAP")
    p.parent.mkdir(mode=0o700,exist_ok=True)
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600)
    os.close(fd)
    with _connect(p) as conn:
        conn.executescript("""
          CREATE TABLE owner (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            schema_version TEXT NOT NULL,
            epoch TEXT NOT NULL,
            graph_id TEXT NOT NULL,
            capacity INTEGER NOT NULL,
            sequence INTEGER NOT NULL);
          CREATE TABLE occupied (
            token TEXT PRIMARY KEY,
            query_ref TEXT NOT NULL UNIQUE,
            receiver_nonce TEXT NOT NULL UNIQUE,
            sequence INTEGER NOT NULL UNIQUE);
        """)
        conn.execute("INSERT INTO owner VALUES(1,?,?,?,?,0)",
                     (SCHEMA,epoch,graph_id,capacity))


class SingleAuthorityResearch:
    """One local owner accessed by multiple hosts; no failover and NO release."""

    def __init__(self,path: str, *, pinned_epoch: str,
                 pinned_graph_id: str, minimum_sequence: int):
        self.path=_path(path)
        if (not _uuid4(pinned_epoch) or type(pinned_graph_id) is not str
            or len(pinned_graph_id)!=64
            or any(x not in "0123456789abcdef" for x in pinned_graph_id)
            or type(minimum_sequence) is not int or minimum_sequence<0):
            raise ValueError("EXTERNALLY_PINNED_OWNER_REQUIRED")
        self.epoch=pinned_epoch
        self.graph=pinned_graph_id
        # The caller's sequence watermark MUST come from independent trusted
        # storage to provide any rollback detection; 0 is not protection.
        self.floor=minimum_sequence
        self.identity=self._identity()
        with _connect(self.path) as conn:
            self._validate(conn)

    def _identity(self):
        st=self.path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
            or st.st_mode & 0o077):
            raise ValueError("UNSAFE_AUTHORITY_FILE")
        return st.st_dev,st.st_ino

    def _validate(self,conn):
        if self._identity()!=self.identity:
            raise ValueError("RESEARCH_AUTHORITY_INODE_CHANGED")
        row=conn.execute("SELECT schema_version,epoch,graph_id,capacity,sequence "
                         "FROM owner WHERE singleton=1").fetchone()
        if (not row or row[:3]!=(SCHEMA,self.epoch,self.graph)
            or type(row[3]) is not int or not 1<=row[3]<=4
            or type(row[4]) is not int or row[4]<self.floor
            or conn.execute("SELECT count(*) FROM owner").fetchone()[0]!=1):
            raise ValueError("OWNER_EPOCH_GRAPH_OR_CHECKPOINT_UNTRUSTED")
        return row[3],row[4]

    def snapshot(self) -> dict:
        with _connect(self.path) as conn:
            cap,seq=self._validate(conn)
            active=conn.execute("SELECT count(*) FROM occupied").fetchone()[0]
            return {"schema":SCHEMA,"epoch":self.epoch,"graph_id":self.graph,
                    "sequence":seq,"active":active,"capacity":cap}

    def admit(self,query_ref: str) -> Decision:
        if (type(query_ref) is not str or not 1<=len(query_ref)<=128
            or any(not (c.isascii() and (c.isalnum() or c in "-_:.")) for c in query_ref)):
            return Decision("invalid-query-ref",None,None,None,None)
        try:
            with _connect(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                capacity,sequence=self._validate(conn)
                active=conn.execute("SELECT count(*) FROM occupied").fetchone()[0]
                if active>=capacity:
                    conn.rollback()
                    return Decision("full",None,None,sequence,active)
                if conn.execute("SELECT 1 FROM occupied WHERE query_ref=?",
                                (query_ref,)).fetchone():
                    conn.rollback()
                    return Decision("duplicate-query-ref",None,None,sequence,active)
                token=uuid.uuid4().hex
                nonce=str(uuid.uuid4())
                next_sequence=sequence+1
                conn.execute("INSERT INTO occupied VALUES(?,?,?,?)",
                             (token,query_ref,nonce,next_sequence))
                conn.execute("UPDATE owner SET sequence=? WHERE singleton=1",
                             (next_sequence,))
                conn.commit()
                self.floor=max(self.floor,next_sequence)
                if self._identity()!=self.identity:
                    return Decision("authority-identity-uncertain",None,None,None,None)
                return Decision("admitted",token,nonce,next_sequence,active+1)
        except (OSError,ValueError,sqlite3.Error):
            # Fail closed under DB I/O error, lock contention, missing owner,
            # rollback or epoch mismatch. Never create or fall back to a copy.
            return Decision("authority-unavailable",None,None,None,None)
