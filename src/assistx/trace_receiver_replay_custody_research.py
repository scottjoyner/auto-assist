"""Research-only receiver trust pin + durable local replay evidence custody.

NO production route imports this module. Acceptance means one laboratory
receiver receipt was authenticated and recorded exactly once, NOT that Neo4j
has stopped everywhere or that a physical admission reservation may be freed.

The caller provides a *pretrusted*, externally pinned Ed25519 public key and
the independently approved SHA-256 digest of that key; neither is recovered
from the receipt. A durable SQLite journal prevents duplicate consumption
*while the same authoritative file survives*. It does not supply cross-host
consensus, protection from filesystem rollback after restart, key escrow,
revocation, signed operator decisions, or production release authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote
import uuid

from .trace_receiver_evidence_research import canonical, verify_research_evidence

SCHEMA="assistx-receiver-consumption-local-research-v1"
FILENAME="receiver-replay-test.sqlite"


@dataclass(frozen=True)
class CustodyDecision:
    accepted: bool
    reason: str
    total_recorded: int | None


def _uuid4(value: str) -> bool:
    try:
        return type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).version == 4
    except (ValueError, TypeError, AttributeError):
        return False


def _path_guard(path: Path) -> None:
    if (not path.is_absolute() or path.name != FILENAME
        or not path.parent.name.startswith("assistx-receiver-replay-test-")
        or path.parent.parent != Path("/tmp") or path.parent.is_symlink()
        or path.is_symlink()):
        raise ValueError("DISPOSABLE_RESEARCH_PATH_REQUIRED")


def _connect(path: Path) -> sqlite3.Connection:
    # Never create an absent database: no restart-induced rearm.
    connection=sqlite3.connect("file:"+quote(str(path),safe="/")+"?mode=rw",
                               uri=True, isolation_level=None, timeout=1)
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=1000")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def bootstrap_disposable_custody(path: str, epoch: str, graph_id: str, key_digest: str) -> None:
    """One-time fixture only. Must NOT be called by a receiver or API startup."""
    p=Path(path)
    _path_guard(p)
    if (not _uuid4(epoch) or type(graph_id) is not str or len(graph_id)!=64
        or any(c not in "0123456789abcdef" for c in graph_id)
        or type(key_digest) is not str or len(key_digest)!=64
        or any(c not in "0123456789abcdef" for c in key_digest)
        or p.exists()):
        raise ValueError("RESEARCH_BOOTSTRAP_REFUSED")
    p.parent.mkdir(mode=0o700, exist_ok=True)
    fd=os.open(p, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with _connect(p) as c:
        c.executescript("""
            CREATE TABLE custody_meta (
                epoch TEXT NOT NULL PRIMARY KEY,
                graph_id TEXT NOT NULL,
                approved_signer_digest TEXT NOT NULL,
                schema_version TEXT NOT NULL);
            CREATE TABLE consumed (
                epoch TEXT NOT NULL,
                receiver_nonce TEXT NOT NULL PRIMARY KEY,
                admission_token TEXT NOT NULL UNIQUE,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                graph_id TEXT NOT NULL,
                server_transaction_id TEXT NOT NULL,
                query_ref TEXT NOT NULL);
        """)
        c.execute("INSERT INTO custody_meta VALUES (?,?,?,?)",
                  (epoch,graph_id,key_digest,SCHEMA))


class ReceiverReplayCustody:
    """One-time *observation record*, NEVER an authorization to release a slot."""

    def __init__(self, path: str, *, expected_epoch: str,
                 expected_graph_id: str, trusted_public_key: bytes,
                 approved_signer_sha256: str, external_minimum_count: int = 0):
        p=Path(path)
        _path_guard(p)
        if (not _uuid4(expected_epoch) or type(expected_graph_id) is not str
            or len(expected_graph_id)!=64 or
            any(c not in "0123456789abcdef" for c in expected_graph_id)
            or type(trusted_public_key) is not bytes or len(trusted_public_key)!=32
            or type(approved_signer_sha256) is not str or len(approved_signer_sha256)!=64
            or any(c not in "0123456789abcdef" for c in approved_signer_sha256)
            or type(external_minimum_count) is not int or external_minimum_count<0):
            raise ValueError("EXPLICIT_EXTERNAL_RECEIVER_TRUST_REQUIRED")
        if hashlib.sha256(trusted_public_key).hexdigest()!=approved_signer_sha256:
            raise ValueError("RECEIVER_PUBLIC_KEY_NOT_APPROVED")
        self.path=p
        self.epoch=expected_epoch
        self.graph_id=expected_graph_id
        self.public_key=trusted_public_key
        self.digest=approved_signer_sha256
        # The floor must come from a separate trusted authority. A caller
        # passing zero does NOT protect against restored old DB copies.
        self.min_count=external_minimum_count
        self._identity=self._on_disk_identity()
        with _connect(self.path) as conn:
            self._validate(conn)

    def _on_disk_identity(self)->tuple[int,int]:
        st=self.path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1
            or st.st_mode & 0o077):
            raise ValueError("UNSAFE_RECEIVER_REPLAY_FILE")
        return st.st_dev,st.st_ino

    def _validate(self,conn:sqlite3.Connection)->int:
        if self._on_disk_identity()!=self._identity:
            raise ValueError("RECEIVER_REPLAY_LEDGER_REPLACED")
        row=conn.execute("SELECT graph_id, approved_signer_digest, schema_version "
                         "FROM custody_meta WHERE epoch=?", (self.epoch,)).fetchone()
        if row!=(self.graph_id,self.digest,SCHEMA):
            raise ValueError("REPLAY_EPOCH_GRAPH_OR_SIGNER_MISMATCH")
        count=conn.execute("SELECT count(*) FROM consumed").fetchone()[0]
        if count<self.min_count:
            raise ValueError("REPLAY_JOURNAL_BELOW_PINNED_CHECKPOINT")
        return count

    def record(self, receipt:dict, signature:bytes, *, expected_token:str,
               expected_query_ref:str, expected_transaction_id:str,
               expected_receiver_nonce:str) -> CustodyDecision:
        # Validated receipt data must be supplied by an independent receiver.
        # No receipt-supplied key, no automatic release, no truth inference.
        if not verify_research_evidence(
            receipt, signature, self.public_key,
            expected_epoch=self.epoch,
            expected_graph_container_id=self.graph_id,
            expected_token=expected_token,
            expected_query_ref=expected_query_ref,
            expected_transaction_id=expected_transaction_id,
            expected_receiver_nonce=expected_receiver_nonce,
        ):
            return CustodyDecision(False,"invalid-or-untrusted-receipt",None)
        fingerprint=hashlib.sha256(canonical(receipt)+signature).hexdigest()
        try:
            with _connect(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                count=self._validate(conn)
                # No TTL, no ON CONFLICT REPLACE. A duplicate receipt, token
                # or nonce is rejected and does not create another decision.
                conn.execute(
                    "INSERT INTO consumed VALUES (?,?,?,?,?,?,?)",
                    (self.epoch,receipt["receiver_nonce"],receipt["token"],
                     fingerprint,receipt["graph_container_id"],
                     receipt["server_transaction_id"],receipt["query_ref"]))
                conn.commit()
                # Catch rollback/inode swaps within this process, but do NOT
                # claim globally distributed monotonic checkpoint custody.
                self.min_count=max(self.min_count,count+1)
                if self._on_disk_identity()!=self._identity:
                    return CustodyDecision(False,"journal-identity-uncertain",None)
                return CustodyDecision(True,"observation-recorded-not-released",count+1)
        except sqlite3.IntegrityError:
            return CustodyDecision(False,"duplicate-or-replayed-receipt",None)
        except (OSError,ValueError,sqlite3.Error):
            return CustodyDecision(False,"custody-unavailable",None)

    def inspect(self)->int|None:
        try:
            with _connect(self.path) as conn:
                return self._validate(conn)
        except (OSError,ValueError,sqlite3.Error):
            return None
