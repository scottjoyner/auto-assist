"""Two-node 2-of-2 research fencing witness, never physical query authority.

The admitted SQLite owner on x1-370 is NOT permitted to make a research
dispatch decision unless a separately pinned xwing witness has durably
recorded the same epoch, graph, term, sequence, token, nonce and query ref.
If the witness is unavailable, leave the *owner slot occupied* and return
no executable grant. No release method, term promotion or automatic recovery.

This is research only; does not prevent a mutually coordinated rollback of
BOTH hosts or independently copied witness journals. It has no Neo4j calls.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import stat
import uuid
from urllib.parse import quote

SCHEMA="assistx-two-host-fence-witness-research-v1"
FILENAME="witness-test.sqlite"


@dataclass(frozen=True)
class WitnessDecision:
    status: str
    sequence: int | None = None
    term: int | None = None


def _uuid4(s):
    try:
        return type(s) is str and str(uuid.UUID(s))==s and uuid.UUID(s).version==4
    except (ValueError,TypeError,AttributeError):
        return False


def _path(value: str) -> Path:
    p=Path(value)
    if (not p.is_absolute() or p.name!=FILENAME
        or not p.parent.name.startswith("assistx-twohost-fence-test-")
        or p.parent.parent!=Path("/tmp") or p.parent.is_symlink()
        or p.is_symlink()):
        raise ValueError("DISPOSABLE_WITNESS_PATH_REQUIRED")
    return p


def _open(path):
    conn=sqlite3.connect("file:"+quote(str(path),safe="/")+"?mode=rw",
       uri=True,timeout=1.0,isolation_level=None)
    conn.execute("PRAGMA busy_timeout=1000")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def bootstrap_witness(path, *, epoch, graph_id, term):
    p=_path(path)
    if (not _uuid4(epoch) or type(graph_id) is not str or len(graph_id)!=64
        or any(c not in "0123456789abcdef" for c in graph_id)
        or type(term) is not int or not 1<=term<=2**31 or p.exists()):
        raise ValueError("INVALID_EXTERNAL_FENCE_BOOTSTRAP")
    p.parent.mkdir(mode=0o700,exist_ok=True)
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600)
    os.close(fd)
    with _open(p) as conn:
        conn.executescript("""
        CREATE TABLE pin (
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          schema_version TEXT NOT NULL,
          epoch TEXT NOT NULL,
          graph_id TEXT NOT NULL,
          term INTEGER NOT NULL,
          highest_sequence INTEGER NOT NULL);
        CREATE TABLE accepted (
          token TEXT NOT NULL PRIMARY KEY,
          receiver_nonce TEXT NOT NULL UNIQUE,
          query_ref TEXT NOT NULL UNIQUE,
          sequence INTEGER NOT NULL UNIQUE,
          term INTEGER NOT NULL);
        """)
        conn.execute("INSERT INTO pin VALUES(1,?,?,?,?,0)",
                     (SCHEMA,epoch,graph_id,term))


class IndependentResearchWitness:
    """Non-expiring independent host capacity; release deliberately impossible."""

    def __init__(self,path, *, expected_epoch, expected_graph, expected_term,
                 independent_min_sequence=0):
        p=_path(path)
        if (not _uuid4(expected_epoch) or type(expected_graph) is not str
            or len(expected_graph)!=64 or any(c not in "0123456789abcdef" for c in expected_graph)
            or type(expected_term) is not int or expected_term<1
            or type(independent_min_sequence) is not int or independent_min_sequence<0):
            raise ValueError("PINNED_WITNESS_IDENTITY_REQUIRED")
        self.path=p
        self.epoch=expected_epoch
        self.graph=expected_graph
        self.term=expected_term
        self.min_sequence=independent_min_sequence
        self.identity=self._identity()
        with _open(p) as conn:
            self._validate(conn)

    def _identity(self):
        st=self.path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_mode & 0o077:
            raise ValueError("UNSAFE_WITNESS_FILE")
        return st.st_dev,st.st_ino

    def _validate(self,conn):
        if self.identity!=self._identity():
            raise ValueError("WITNESS_INODE_CHANGED")
        row=conn.execute("SELECT schema_version,epoch,graph_id,term,highest_sequence "
                         "FROM pin WHERE singleton=1").fetchone()
        if (row is None or row[:4]!=(SCHEMA,self.epoch,self.graph,self.term)
            or row[4]<self.min_sequence
            or conn.execute("SELECT COUNT(*) FROM pin").fetchone()[0]!=1):
            raise ValueError("WITNESS_TERM_OR_SEQUENCE_UNTRUSTED")
        return row[4]

    def observe(self):
        with _open(self.path) as conn:
            sequence=self._validate(conn)
            occupancy=conn.execute("SELECT COUNT(*) FROM accepted").fetchone()[0]
            return {"schema":SCHEMA, "epoch":self.epoch, "graph_id":self.graph,
                    "term":self.term,"highest_sequence":sequence,"occupied":occupancy}

    def attest(self, *, epoch, graph_id, term, sequence, token, receiver_nonce,
               query_ref) -> WitnessDecision:
        if (epoch!=self.epoch or graph_id!=self.graph or term!=self.term
            or type(sequence) is not int or sequence<1
            or type(token) is not str or len(token)!=32 or
            any(c not in "0123456789abcdef" for c in token)
            or not _uuid4(receiver_nonce) or type(query_ref) is not str
            or not 1<=len(query_ref)<=128 or
            any(not(c.isascii() and (c.isalnum() or c in "-_:.")) for c in query_ref)):
            return WitnessDecision("identity-or-term-denied")
        try:
            with _open(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                previous=self._validate(conn)
                occupied=conn.execute("SELECT COUNT(*) FROM accepted").fetchone()[0]
                # Crucial: witness retains occupancy even when local owner or
                # transport is lost. No TTL; no epoch rollover/term bump here.
                if occupied:
                    conn.rollback()
                    return WitnessDecision("physical-capacity-held",previous,self.term)
                if sequence!=previous+1:
                    conn.rollback()
                    return WitnessDecision("out-of-sequence",previous,self.term)
                conn.execute(
                    "INSERT INTO accepted VALUES (?,?,?,?,?)",
                    (token,receiver_nonce,query_ref,sequence,term))
                conn.execute("UPDATE pin SET highest_sequence=? WHERE singleton=1",
                             (sequence,))
                conn.commit()
                self.min_sequence=max(self.min_sequence,sequence)
                if self._identity()!=self.identity:
                    return WitnessDecision("witness-identity-uncertain")
                return WitnessDecision("witnessed-no-physical-dispatch",sequence,self.term)
        except (sqlite3.Error,OSError,ValueError):
            return WitnessDecision("witness-unavailable")
