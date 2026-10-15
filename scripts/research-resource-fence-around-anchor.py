#!/usr/bin/env python3
"""Disposable SQLite source write fence around synthetic anchor consumption.

Research ONLY. This is NOT cross-database atomicity or a protected resource
authority. A common host/DB admin can replace files, forge source receipts,
alter a public root, or mutate resource after the source lock is released.
No executable release entrypoint; no real DB/network/credential access.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from typing import Any

_CONSUMER = Path(__file__).with_name("research-consume-asymmetric-receipt.py")
_RESOURCE = "auto-router-runtime-projection"


@cache
def _consumer() -> Any:
    spec = importlib.util.spec_from_file_location(
        "research_fenced_receipt_consumer", _CONSUMER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("research_consumer_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_from_open_transaction(
    db: sqlite3.Connection, expected_nonce: Any,
) -> dict[str, Any] | None:
    """Read from the SAME connection holding BEGIN IMMEDIATE.

    No new path open: inode substitution cannot redirect the cursor.
    This is still only local SQL convention, NOT a real resource ACL.
    """
    row = db.execute(
        """SELECT resource_id,term,commit_seq,generation,
                  checksum,revision_sha256,committed
           FROM resource_effect WHERE resource_id=?""", (_RESOURCE,)
    ).fetchone()
    if row is None:
        return None
    receipt = db.execute(
        """SELECT term,commit_seq,generation,checksum,revision_sha256,nonce
           FROM resource_commit_receipts WHERE commit_seq=?""", (row[2],)
    ).fetchone()
    expected_claim = (*row[1:6], expected_nonce)
    if receipt != expected_claim:
        return None
    return dict(zip(
        ("resource_id", "term", "commit_seq", "generation", "checksum",
         "revision_sha256", "committed"),
        (*row[:6], row[6] == 1),
        strict=True,
    ))

def _committed_readback(resource_path: Path, expected_nonce: Any) -> dict[str, Any] | None:
    """Independent observer used for post-run tests, never fenced readback."""
    with sqlite3.connect(
        f"{resource_path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25
    ) as db:
        db.execute("PRAGMA query_only=ON")
        return _read_from_open_transaction(db, expected_nonce)


def _path_identity(resource_path: Path) -> tuple[int, int] | None:
    """Best-effort inode change detection; NOT trusted path ownership."""
    try:
        st = resource_path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def consume_while_source_fenced(
    source_db: Path,
    anchor_db: Path,
    envelope: Any,
    *,
    pinned_public_keys: Mapping[str, bytes] | None,
    expected_effect: Any,
    expected_challenge: Any,
    expected_previous: Any,
    now_ms: int,
    while_source_locked: Callable[[], None] | None = None,
    after_anchor_committed: Callable[[], None] | None = None,
) -> tuple[bool, str]:
    """Hold source BEGIN IMMEDIATE through Ed25519 verification and CAS.

    No source write is committed here; this is a local SQLite write-lock
    demonstration. It cannot replace source-owned physical effect fencing.
    """
    consumer = _consumer()
    original_identity = _path_identity(source_db)
    if original_identity is None:
        return False, "source_resource_unavailable_no_authority"
    try:
        source = sqlite3.connect(
            f"{source_db.resolve().as_uri()}?mode=rw", uri=True, timeout=0.15
        )
    except sqlite3.Error:
        return False, "source_resource_unavailable_no_authority"
    try:
        source.execute("PRAGMA busy_timeout=150")
        source.execute("BEGIN IMMEDIATE")
        if _path_identity(source_db) != original_identity:
            return False, "source_path_identity_changed"
        if while_source_locked is not None:
            while_source_locked()
        if _path_identity(source_db) != original_identity:
            return False, "source_path_identity_changed"
        # Read both observations from the original write-locked connection.
        # Reopening a path here would admit a replacement SQLite inode.
        claim = envelope.get("receipt") if type(envelope) is dict else None
        expected_nonce = claim.get("nonce") if type(claim) is dict else None

        def readback() -> Any:
            return _read_from_open_transaction(source, expected_nonce)

        if readback() != expected_effect:
            return False, "source_committed_effect_or_receipt_missing"
        result = consumer.consume_once(
            anchor_db, envelope, pinned_public_keys=pinned_public_keys,
            expected_effect=expected_effect,
            expected_challenge=expected_challenge,
            expected_previous=expected_previous,
            readback=readback, now_ms=now_ms,
        )
        if result[0]:
            if _path_identity(source_db) != original_identity:
                raise consumer.UncertainReceiptCommit(
                    "SOURCE_PATH_CHANGED_AFTER_ANCHOR_COMMIT_NO_RETRY"
                )
            if after_anchor_committed is not None:
                try:
                    after_anchor_committed()
                except Exception as exc:
                    raise consumer.UncertainReceiptCommit(
                        "SOURCE_FENCE_POST_ANCHOR_OUTCOME_UNCERTAIN_NO_RETRY"
                    ) from exc
            if _path_identity(source_db) != original_identity:
                raise consumer.UncertainReceiptCommit(
                    "SOURCE_PATH_CHANGED_AFTER_ANCHOR_COMMIT_NO_RETRY"
                )
            return True, "RESEARCH_SOURCE_WRITE_LOCK_AND_ANCHOR_CAS_ONLY"
        return result
    except sqlite3.Error:
        return False, "source_write_fence_unavailable"
    finally:
        source.rollback()
        source.close()


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_FENCED_RESOURCE_OR_RELEASE_ENTRYPOINT")
