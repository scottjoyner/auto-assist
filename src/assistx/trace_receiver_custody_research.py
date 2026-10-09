"""RESEARCH ONLY: pinned Ed25519 receipt identity + conservative local custody.

This is NOT fleet-distributed consensus, remote transaction truth, key escrow,
global anti-rollback, or authorization to release a physical-read reservation.
Nothing in production API/router imports this module.

The trusted public key and expected identity tuple come from the caller's
*independently pinned configuration*, never from the receipt envelope.
A separately retained checkpoint is required on reopening. Local SQLite
makes one-time observation custody atomic against simultaneous processes,
not against distributed copies or rollback of BOTH DB and checkpoint.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote
import uuid

from .trace_receiver_evidence_research import (
    canonical, verify_research_evidence,
)

SCHEMA = "assistx-receiver-custody-local-research-v1"
NAME = "receiver-custody-test.sqlite"
ROOT = Path("/tmp")
PREFIX = "assistx-trace-custody-test-"


@dataclass(frozen=True)
class PinnedCheckpoint:
    sequence: int
    head_sha256: str


@dataclass(frozen=True)
class CustodyDecision:
    accepted: bool
    reason: str
    checkpoint: PinnedCheckpoint | None = None


def _uuid4(text: str) -> bool:
    try:
        return type(text) is str and uuid.UUID(text).version == 4 and str(uuid.UUID(text)) == text
    except (TypeError, ValueError, AttributeError):
        return False


def _hex64(text: str) -> bool:
    return type(text) is str and len(text) == 64 and all(x in "0123456789abcdef" for x in text)


def _key_hash(key: bytes) -> str:
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("EXTERNAL_PINNED_ED25519_PUBLIC_KEY_REQUIRED")
    return hashlib.sha256(key).hexdigest()


def _genesis(epoch: str, key_hash: str) -> PinnedCheckpoint:
    genesis = hashlib.sha256((SCHEMA + "|" + epoch + "|" + key_hash).encode("ascii")).hexdigest()
    return PinnedCheckpoint(0, genesis)


def _valid_checkpoint(cp: PinnedCheckpoint) -> bool:
    return (
        type(cp) is PinnedCheckpoint and type(cp.sequence) is int and cp.sequence >= 0
        and _hex64(cp.head_sha256)
    )


def _connection(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=rw",
                        uri=True, timeout=1, isolation_level=None)
    c.execute("PRAGMA trusted_schema=OFF")
    c.execute("PRAGMA synchronous=FULL")
    c.execute("PRAGMA busy_timeout=1000")
    return c


def bootstrap_disposable_receiver_custody(
    path: str, epoch: str, pinned_public_key: bytes
) -> PinnedCheckpoint:
    """Explicit one-shot test-file creation; never called by an API process."""
    p = Path(path)
    key_hash = _key_hash(pinned_public_key)
    if (
        not p.is_absolute() or p.name != NAME
        or not p.parent.name.startswith(PREFIX) or p.parent.parent != ROOT
        or p.parent.is_symlink() or p.exists() or p.is_symlink()
        or not _uuid4(epoch)
    ):
        raise ValueError("RESEARCH_CUSTODY_BOOTSTRAP_REFUSED")
    p.parent.mkdir(mode=0o700, exist_ok=True)
    dir_stat = p.parent.lstat()
    if (
        not stat.S_ISDIR(dir_stat.st_mode)
        or dir_stat.st_uid != os.geteuid()
        or stat.S_IMODE(dir_stat.st_mode) != 0o700
    ):
        raise ValueError("INVALID_CUSTODY_DIRECTORY")
    fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    os.close(fd)
    cp = _genesis(epoch, key_hash)
    with closing(_connection(p)) as conn:
        conn.executescript("""
        CREATE TABLE custody_meta (
            epoch TEXT PRIMARY KEY NOT NULL,
            public_key_sha256 TEXT NOT NULL,
            schema_version TEXT NOT NULL
        );
        CREATE TABLE progress (
            id INTEGER PRIMARY KEY CHECK(id=1),
            sequence INTEGER NOT NULL CHECK(sequence>=0),
            head_sha256 TEXT NOT NULL
        );
        CREATE TABLE received (
            receiver_nonce TEXT PRIMARY KEY NOT NULL,
            token TEXT NOT NULL UNIQUE,
            graph_transaction_key TEXT NOT NULL UNIQUE,
            receipt_sha256 TEXT NOT NULL UNIQUE,
            sequence INTEGER NOT NULL UNIQUE
        );
        """)
        conn.execute("INSERT INTO custody_meta VALUES (?,?,?)", (epoch, key_hash, SCHEMA))
        conn.execute("INSERT INTO progress VALUES (1,0,?)", (cp.head_sha256,))
    return cp


class ReceiverReceiptCustody:
    """Pessimistic local observation custody; NO admission slot release method."""

    def __init__(
        self, path: str, *, expected_epoch: str,
        operator_pinned_public_key: bytes, trusted_checkpoint: PinnedCheckpoint
    ):
        if not _uuid4(expected_epoch) or not _valid_checkpoint(trusted_checkpoint):
            raise ValueError("EXTERNAL_EPOCH_AND_CHECKPOINT_REQUIRED")
        self.path = Path(path)
        if (
            not self.path.is_absolute() or self.path.name != NAME
            or self.path.parent.parent != ROOT
            or not self.path.parent.name.startswith(PREFIX)
            or self.path.parent.is_symlink()
        ):
            raise ValueError("ONLY_DISPOSABLE_RESEARCH_CUSTODY_PATH_ALLOWED")
        directory = self.path.parent.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or directory.st_uid != os.geteuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
        ):
            raise ValueError("UNSAFE_CUSTODY_DIRECTORY")
        self.epoch = expected_epoch
        self.public_key = operator_pinned_public_key
        self.key_hash = _key_hash(operator_pinned_public_key)
        self._checkpoint = trusted_checkpoint
        self._identity = self._file_identity()
        with closing(_connection(self.path)) as conn:
            if self._verify_state(conn) != trusted_checkpoint:
                raise ValueError("EXTERNAL_CHECKPOINT_MISMATCH")

    def _file_identity(self) -> tuple[int, int]:
        info = self.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & 0o077
        ):
            raise ValueError("UNSAFE_CUSTODY_FILE")
        return (info.st_dev, info.st_ino)

    def _verify_state(self, conn: sqlite3.Connection) -> PinnedCheckpoint:
        if self._file_identity() != self._identity:
            raise ValueError("CUSTODY_FILE_REPLACED")
        meta = conn.execute("SELECT epoch,public_key_sha256,schema_version FROM custody_meta").fetchall()
        if meta != [(self.epoch, self.key_hash, SCHEMA)]:
            raise ValueError("CUSTODY_AUTHORITY_CHANGED")
        progress = conn.execute("SELECT sequence,head_sha256 FROM progress WHERE id=1").fetchall()
        if len(progress) != 1 or type(progress[0][0]) is not int or not _hex64(progress[0][1]):
            raise ValueError("CUSTODY_CHECKPOINT_CORRUPT")
        seq, head = progress[0]
        # Check every stored receipt commitment, not merely a row count.
        # The externally pinned head is the trust anchor across process
        # restarts; a replaced/reordered/tampered local receipt must deny.
        rows = conn.execute(
            "SELECT sequence,receipt_sha256 FROM received ORDER BY sequence"
        ).fetchall()
        if len(rows) != seq:
            raise ValueError("CUSTODY_SEQUENCE_INCONSISTENT")
        current = _genesis(self.epoch, self.key_hash).head_sha256
        for expected_index, (index, receipt_digest) in enumerate(rows, start=1):
            if type(index) is not int or index != expected_index or not _hex64(receipt_digest):
                raise ValueError("CUSTODY_RECEIPT_SEQUENCE_INVALID")
            current = hashlib.sha256(
                (current + ":" + str(index) + ":" + receipt_digest).encode("ascii")
            ).hexdigest()
        if current != head:
            raise ValueError("CUSTODY_HASHCHAIN_DIVERGED")
        return PinnedCheckpoint(seq, head)

    @property
    def checkpoint(self) -> PinnedCheckpoint:
        return self._checkpoint

    def observe_once(
        self, evidence: dict, signature: bytes, *,
        expected_token: str, expected_query_ref: str,
        expected_graph_container_id: str, expected_transaction_id: str,
        expected_receiver_nonce: str,
    ) -> CustodyDecision:
        """Record a verified observation ONCE, not remote closure authority.

        If the checkpoint is stale or local state is unavailable, deny without
        creating a fresh ledger, skipping a receipt, or reclaiming a query.
        """
        valid = verify_research_evidence(
            evidence, signature, self.public_key,
            expected_epoch=self.epoch,
            expected_token=expected_token, expected_query_ref=expected_query_ref,
            expected_graph_container_id=expected_graph_container_id,
            expected_transaction_id=expected_transaction_id,
            expected_receiver_nonce=expected_receiver_nonce,
        )
        if valid is not True:
            return CustodyDecision(False, "invalid_or_untrusted_receipt")
        try:
            receipt_digest = hashlib.sha256(canonical(evidence) + signature).hexdigest()
            graph_transaction_key = expected_graph_container_id + ":" + expected_transaction_id
            with closing(_connection(self.path)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                actual = self._verify_state(conn)
                if actual != self._checkpoint:
                    conn.rollback()
                    return CustodyDecision(False, "checkpoint_mismatch")
                next_seq = actual.sequence + 1
                next_digest = hashlib.sha256(
                    (actual.head_sha256 + ":" + str(next_seq) + ":" + receipt_digest).encode("ascii")
                ).hexdigest()
                try:
                    conn.execute(
                        "INSERT INTO received VALUES (?,?,?,?,?)",
                        (expected_receiver_nonce, expected_token, graph_transaction_key,
                         receipt_digest, next_seq),
                    )
                except sqlite3.IntegrityError:
                    conn.rollback()
                    return CustodyDecision(False, "already_consumed_or_collision")
                conn.execute(
                    "UPDATE progress SET sequence=?,head_sha256=? WHERE id=1",
                    (next_seq, next_digest),
                )
                conn.commit()
                # A post-commit inode change is uncertainty, never success.
                if self._file_identity() != self._identity:
                    return CustodyDecision(False, "file_identity_uncertain")
                latest = PinnedCheckpoint(next_seq, next_digest)
                self._checkpoint = latest
                return CustodyDecision(True, "recorded_no_release", latest)
        except (OSError, ValueError, sqlite3.Error):
            return CustodyDecision(False, "unavailable")

    def inspect(self) -> PinnedCheckpoint | None:
        try:
            with closing(_connection(self.path)) as conn:
                cp = self._verify_state(conn)
                return cp if cp == self._checkpoint else None
        except (OSError, ValueError, sqlite3.Error):
            return None
