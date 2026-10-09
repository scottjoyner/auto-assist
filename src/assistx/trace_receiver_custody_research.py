"""Research-only pinned receiver trust and one-time evidence custody.

This is NOT admission permission, physical closure truth, replicated
consensus, key escrow, nor a production release mechanism. No runtime route
imports it. It deliberately has no API to release a physical-query slot.

Trust is passed independently at construction, never read from an incoming
receipt. A single pre-existing LOCAL SQLite file atomically registers a
previously verified record once, with fail-closed path/schema/epoch checks.
File snapshot rollback or copies can replay receipts: global authority is
NOT established.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

from .trace_receiver_evidence_research import (
    HEX64, _uuid4, canonical, verify_research_evidence,
)

SCHEMA = "assistx-receiver-custody-local-research-v1"
DB_NAME = "receiver-custody.sqlite"


def _connect(path: Path):
    conn = sqlite3.connect(
        "file:" + quote(str(path), safe="/") + "?mode=rw",
        uri=True, isolation_level=None, timeout=0.3,
    )
    conn.execute("PRAGMA busy_timeout=300")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def _path_identity(path: Path):
    info = path.lstat()  # symlinks rejected even if they point to valid DB
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("UNSAFE_CUSTODY_FILE")
    return info.st_dev, info.st_ino


def _validate_inputs(epoch: str, graph_id: str, public_key: bytes):
    if not _uuid4(epoch):
        raise ValueError("EXPECTED_EPOCH_REQUIRED")
    if type(graph_id) is not str or not HEX64.fullmatch(graph_id):
        raise ValueError("PINNED_GRAPH_ID_REQUIRED")
    if type(public_key) is not bytes or len(public_key) != 32:
        raise ValueError("EXTERNALLY_PINNED_RECEIVER_PUBLIC_KEY_REQUIRED")


def bootstrap_disposable_custody(path: str, epoch: str,
                                 graph_id: str, public_key: bytes):
    """Never called by runtime; create-once under a disposable /tmp directory."""
    _validate_inputs(epoch, graph_id, public_key)
    p = Path(path)
    if (not p.is_absolute() or p.parent.parent != Path("/tmp")
        or not p.parent.name.startswith("assistx-receipt-test-")
        or p.name != DB_NAME or p.parent.is_symlink()
        or p.exists() or p.is_symlink()):
        raise ValueError("RESEARCH_FIXTURE_ONLY")
    if not p.parent.exists():
        p.parent.mkdir(mode=0o700)
    fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with _connect(p) as conn:
        conn.executescript("""
            CREATE TABLE custody_meta (
                epoch TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                graph_id TEXT NOT NULL,
                receiver_key_sha256 TEXT NOT NULL);
            CREATE TABLE recorded_evidence (
                epoch TEXT NOT NULL,
                receiver_nonce TEXT NOT NULL,
                token TEXT NOT NULL,
                query_ref TEXT NOT NULL,
                graph_id TEXT NOT NULL,
                server_transaction_id TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL UNIQUE,
                PRIMARY KEY (epoch, receiver_nonce),
                UNIQUE (epoch, token),
                UNIQUE (epoch, graph_id, server_transaction_id));
        """)
        conn.execute("INSERT INTO custody_meta VALUES (?, ?, ?, ?)", (
            epoch, SCHEMA, graph_id, hashlib.sha256(public_key).hexdigest(),
        ))


@dataclass(frozen=True)
class CustodyDecision:
    recorded: bool
    reason: str


class LocalReceiverReceiptCustodyResearch:
    def __init__(self, path: str, epoch: str,
                 graph_id: str, independently_pinned_public_key: bytes):
        """The independent public key is a required constructor argument."""
        _validate_inputs(epoch, graph_id, independently_pinned_public_key)
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("ABSOLUTE_CUSTODY_PATH_REQUIRED")
        self.epoch = epoch
        self.graph_id = graph_id
        self._trusted_key = independently_pinned_public_key
        self._key_digest = hashlib.sha256(self._trusted_key).hexdigest()
        self._identity = _path_identity(self.path)
        with _connect(self.path) as conn:
            self._validate(conn)

    def _validate(self, conn):
        if _path_identity(self.path) != self._identity:
            raise ValueError("CUSTODY_FILE_CHANGED")
        row = conn.execute(
            "SELECT schema_version, graph_id, receiver_key_sha256 "
            "FROM custody_meta WHERE epoch=?", (self.epoch,),
        ).fetchone()
        if row != (SCHEMA, self.graph_id, self._key_digest):
            raise ValueError("CUSTODY_EPOCH_GRAPH_OR_KEY_MISMATCH")

    def record_observation_once(
        self, receipt: dict, signature: bytes, *,
        expected_token: str, expected_query_ref: str,
        expected_transaction_id: str, expected_receiver_nonce: str,
    ) -> CustodyDecision:
        """Permanently record one verified research observation, not release.

        Caller must bring independently pinned expected IDs from a separate
        authoritative request/graph record; this helper cannot establish them.
        """
        verified = verify_research_evidence(
            receipt, signature, self._trusted_key,
            expected_epoch=self.epoch, expected_token=expected_token,
            expected_query_ref=expected_query_ref,
            expected_graph_container_id=self.graph_id,
            expected_transaction_id=expected_transaction_id,
            expected_receiver_nonce=expected_receiver_nonce,
        )
        if not verified:
            return CustodyDecision(False, "signature_or_binding_denied")
        digest = hashlib.sha256(canonical(receipt) + signature).hexdigest()
        try:
            with _connect(self.path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._validate(conn)
                conn.execute("""
                    INSERT INTO recorded_evidence
                    (epoch,receiver_nonce,token,query_ref,graph_id,
                     server_transaction_id,evidence_sha256)
                    VALUES (?,?,?,?,?,?,?)
                    """, (
                        self.epoch, expected_receiver_nonce, expected_token,
                        expected_query_ref, self.graph_id,
                        expected_transaction_id, digest,
                    ))
                conn.commit()
                if _path_identity(self.path) != self._identity:
                    return CustodyDecision(False, "custody_identity_uncertain")
                return CustodyDecision(True, "recorded_only_not_released")
        except sqlite3.IntegrityError:
            return CustodyDecision(False, "duplicate_or_replayed_receipt")
        except (OSError, ValueError, sqlite3.Error):
            return CustodyDecision(False, "custody_unavailable")

    def count(self):
        try:
            with _connect(self.path) as conn:
                self._validate(conn)
                return conn.execute("SELECT count(*) FROM recorded_evidence").fetchone()[0]
        except (OSError, ValueError, sqlite3.Error):
            return None
