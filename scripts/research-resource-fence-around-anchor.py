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


def _committed_readback(resource_path: Path, expected_nonce: Any) -> dict[str, Any] | None:
    """Fresh read-only connection sees COMMITTED rows, not writer's changes.

    Resource effect and matching receipt must share the exact revision,
    generation, checksum and sequence. A privileged DBA can forge both.
    """
    with sqlite3.connect(
        f"{resource_path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.25
    ) as db:
        db.execute("PRAGMA query_only=ON")
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
    try:
        source = sqlite3.connect(
            f"{source_db.resolve().as_uri()}?mode=rw", uri=True, timeout=0.15
        )
    except sqlite3.Error:
        return False, "source_resource_unavailable_no_authority"
    try:
        source.execute("PRAGMA busy_timeout=150")
        source.execute("BEGIN IMMEDIATE")
        if while_source_locked is not None:
            while_source_locked()
        # Double resource readback inside the anchor verifier uses a fresh
        # RO connection, and observes only fully committed resource state.
        claim = envelope.get("receipt") if type(envelope) is dict else None
        expected_nonce = claim.get("nonce") if type(claim) is dict else None

        def readback() -> Any:
            return _committed_readback(source_db, expected_nonce)

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
            if after_anchor_committed is not None:
                try:
                    after_anchor_committed()
                except Exception as exc:
                    raise consumer.UncertainReceiptCommit(
                        "SOURCE_FENCE_POST_ANCHOR_OUTCOME_UNCERTAIN_NO_RETRY"
                    ) from exc
            return True, "RESEARCH_SOURCE_WRITE_LOCK_AND_ANCHOR_CAS_ONLY"
        return result
    except sqlite3.Error:
        return False, "source_write_fence_unavailable"
    finally:
        source.rollback()
        source.close()


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_FENCED_RESOURCE_OR_RELEASE_ENTRYPOINT")
