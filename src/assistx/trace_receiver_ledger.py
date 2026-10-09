"""Receiver-local fsynced receipt chain with external-head anti-rollback gate.

RESEARCH ONLY. This is ordinary owner-writable ext4, not WORM. The trusted
expected next sequence and head MUST come from a separate nonrewindable
authority, never from the writable ledger or proposed incoming receipt.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .trace_asymmetric_custody import GENESIS, verify_dual_authority_receipt
from .trace_execution_adapter import TraceDenied
from .trace_segment_bundle import _private, _read
from .trace_segment_plan import canonical

NAME = "receiver-receipts.jsonl"
LOCK = ".receiver-ledger.lock"
MAX_LEDGER = 16 * 1024 * 1024
MAX_RECEIPT = 16 * 1024
MIN_DISK_FREE = 8 * 1024 * 1024


def _fd_is_owner_private(fd: int) -> bool:
    s = os.fstat(fd)
    return stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1 and not s.st_mode & 0o077


def _lock(root: Path) -> int:
    try:
        fd = os.open(root / LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise TraceDenied("receiver_lock_unavailable") from exc
    if not _fd_is_owner_private(fd):
        os.close(fd)
        raise TraceDenied("receiver_lock_unsafe")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except OSError as exc:
        os.close(fd)
        raise TraceDenied("receiver_lock_unavailable") from exc


def _verify_row(
    row: Any,
    *,
    sequence: int,
    previous: str,
    witness_verifier: Ed25519PublicKey,
    witness_id: str,
    producer_id: str,
    producer_key_id: str,
) -> str:
    if not isinstance(row, dict):
        raise TraceDenied("receiver_ledger_row_invalid")
    try:
        return verify_dual_authority_receipt(
            row,
            witness_verifier=witness_verifier,
            witness_id=witness_id,
            producer_id=producer_id,
            producer_key_id=producer_key_id,
            expected_producer_manifest_sha256=row["producer_manifest_sha256"],
            node_id=row["node_id"],
            expected_journal_sha256=row["journal_sha256"],
            expected_index_sha256=row["index_sha256"],
            expected_sequence=sequence,
            expected_previous_receipt_sha256=previous,
            minimum_observed_at_ms=0,
        )
    except (KeyError, TypeError) as exc:
        raise TraceDenied("receiver_ledger_row_invalid") from exc


def read_receiver_ledger(
    root: Path,
    *,
    witness_verifier: Ed25519PublicKey,
    witness_id: str,
    producer_id: str,
    producer_key_id: str,
) -> tuple[int, str]:
    """Revalidate every stored signature, sequence and predecessor digest.

    This does not prove the tail has not been deleted. Compare the returned
    state to a separately secured checkpoint before trusting it.
    """
    _private(root)
    path = root / NAME
    if not path.exists():
        return 0, GENESIS
    raw = _read(path, MAX_LEDGER)
    if raw and not raw.endswith(b"\n"):
        raise TraceDenied("receiver_ledger_torn_row")
    seq, head = 0, GENESIS
    for line in raw.splitlines():
        if len(line) > MAX_RECEIPT or not line:
            raise TraceDenied("receiver_ledger_row_invalid")
        try:
            row = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise TraceDenied("receiver_ledger_row_invalid") from exc
        head = _verify_row(
            row,
            sequence=seq + 1,
            previous=head,
            witness_verifier=witness_verifier,
            witness_id=witness_id,
            producer_id=producer_id,
            producer_key_id=producer_key_id,
        )
        seq += 1
    return seq, head


def append_receiver_receipt(
    root: Path,
    *,
    receipt: dict[str, Any],
    witness_verifier: Ed25519PublicKey,
    witness_id: str,
    producer_id: str,
    producer_key_id: str,
    external_expected_sequence: int,
    external_expected_previous_digest: str,
    expected_manifest_sha256: str,
    expected_node_id: str,
    expected_journal_sha256: str,
    expected_index_sha256: str,
    minimum_observed_at_ms: int,
) -> dict[str, Any]:
    """Fsync one independently signed receipt with serial execution.

    Caller must provide the protected external high-water state and persist
    the returned digest OUTSIDE this writable trust domain. The latter
    write is not atomic with this append; failure is safe-to-deny on retry.
    """
    _private(root)
    if type(external_expected_sequence) is not int or external_expected_sequence <= 0:
        raise TraceDenied("receiver_expected_sequence_invalid")
    lock_fd = _lock(root)
    try:
        seq, last = read_receiver_ledger(
            root,
            witness_verifier=witness_verifier,
            witness_id=witness_id,
            producer_id=producer_id,
            producer_key_id=producer_key_id,
        )
        if seq + 1 != external_expected_sequence or last != external_expected_previous_digest:
            raise TraceDenied("receiver_external_anchor_mismatch")
        signed_digest = verify_dual_authority_receipt(
            receipt,
            witness_verifier=witness_verifier,
            witness_id=witness_id,
            producer_id=producer_id,
            producer_key_id=producer_key_id,
            expected_producer_manifest_sha256=expected_manifest_sha256,
            node_id=expected_node_id,
            expected_journal_sha256=expected_journal_sha256,
            expected_index_sha256=expected_index_sha256,
            expected_sequence=external_expected_sequence,
            expected_previous_receipt_sha256=external_expected_previous_digest,
            minimum_observed_at_ms=minimum_observed_at_ms,
        )
        encoded = canonical(receipt) + b"\n"
        if len(encoded) > MAX_RECEIPT:
            raise TraceDenied("receiver_receipt_too_large")
        if os.statvfs(root).f_bavail * os.statvfs(root).f_frsize < MIN_DISK_FREE:
            raise TraceDenied("receiver_disk_pressure")
        path = root / NAME
        try:
            fd = os.open(
                path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
            )
        except OSError as exc:
            raise TraceDenied("receiver_ledger_unavailable") from exc
        try:
            if not _fd_is_owner_private(fd):
                raise TraceDenied("receiver_ledger_unsafe")
            if os.fstat(fd).st_size + len(encoded) > MAX_LEDGER:
                raise TraceDenied("receiver_ledger_full")
            if os.write(fd, encoded) != len(encoded):
                raise TraceDenied("receiver_ledger_short_write")
            os.fsync(fd)
        finally:
            os.close(fd)
        rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(rootfd)
        finally:
            os.close(rootfd)
        return {
            "ok": True,
            "sequence": external_expected_sequence,
            "receipt_sha256": signed_digest,
            "receiver_local_fsynced": True,
            "independent_anchor_fsynced": False,
            "worm_storage_proven": False,
        }
    finally:
        os.close(lock_fd)
