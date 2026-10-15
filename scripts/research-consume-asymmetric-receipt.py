#!/usr/bin/env python3
"""Disposable SQLite CAS + prior Ed25519 readback, NOT production admission.

No operational CLI. State is local, caller-supplied SQLite path under pytest
tmp_path. The verifier's external root, expected effect and resource readback
MUST be genuinely independent in a real system; this module cannot prove it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_READER = Path(__file__).with_name("research-asymmetric-resource-receipt.py")


class UncertainReceiptCommit(Exception):
    """Durable anchor advance may have succeeded, ACK not observed."""


def _verifier() -> Any:
    """Load only the pinned sibling research verifier, no supplied path."""
    spec = importlib.util.spec_from_file_location("research_asymmetric_receipt", _READER)
    if spec is None or spec.loader is None:
        raise RuntimeError("research_verifier_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def initialize_disposable_anchor(path: Path, *, key_epoch: int = 7) -> None:
    """Create ONLY a new database, refuse replacement of any existing file."""
    if path.exists() or not path.parent.is_dir() or type(key_epoch) is not int or key_epoch < 1:
        raise ValueError("disposable_anchor_bootstrap_denied")
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE highwater (
          resource_id TEXT PRIMARY KEY, term INTEGER NOT NULL,
          commit_seq INTEGER NOT NULL, last_nonce TEXT NOT NULL,
          pinned_key_epoch INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE spent_challenges (
          challenge TEXT PRIMARY KEY, receipt_digest TEXT NOT NULL,
          term INTEGER NOT NULL, commit_seq INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE consumed_receipts (
          receipt_digest TEXT PRIMARY KEY,
          challenge TEXT NOT NULL UNIQUE,
          term INTEGER NOT NULL, commit_seq INTEGER NOT NULL)""")
        conn.execute(
            "INSERT INTO highwater VALUES (?,?,?,?,?)",
            ("auto-router-runtime-projection", 4, 11, "c" * 32, key_epoch),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_anchor(path: Path) -> dict[str, Any]:
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        row = conn.execute(
            "SELECT term,commit_seq,last_nonce,pinned_key_epoch FROM highwater "
            "WHERE resource_id='auto-router-runtime-projection'"
        ).fetchone()
    if row is None:
        raise ValueError("anchor_missing")
    return dict(zip(("term", "commit_seq", "last_nonce", "pinned_key_epoch"), row, strict=True))


def consume_once(
    path: Path,
    envelope: Any,
    *,
    pinned_public_keys: Mapping[str, bytes] | None,
    expected_effect: Any,
    expected_challenge: Any,
    expected_previous: Any,
    readback: Callable[[], Any] | None,
    now_ms: int,
    lose_ack_after_commit: bool = False,
) -> tuple[bool, str]:
    """Verify THEN atomically consume only if anchor state is unchanged.

    Concurrent consumers serialize under BEGIN IMMEDIATE. Only this local
    source's synthetic anchor is protected. External SQL/snapshot admin can
    still reset it. No automatic retry on uncertain outcomes.
    """
    if (
        type(expected_previous) is not dict
        or set(expected_previous) != {"term", "commit_seq", "last_nonce"}
        or not callable(readback)
    ):
        return False, "external_custody_or_readback_missing"
    # A consumer must never recreate a missing/restored anchor with a new,
    # silently empty SQLite file. Bootstrap happens only in a separate
    # explicitly disposable research setup function.
    try:
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=rw", uri=True, timeout=2.0
        )
    except sqlite3.Error:
        return False, "anchor_unavailable_no_authority"
    did_commit = False
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT term,commit_seq,last_nonce,pinned_key_epoch FROM highwater "
            "WHERE resource_id='auto-router-runtime-projection'"
        ).fetchone()
        if current is None:
            return False, "anchor_missing"
        prior = dict(zip(("term", "commit_seq", "last_nonce"), current[:3], strict=True))
        if prior != expected_previous:
            return False, "stale_or_restored_anchor_cas"
        claim = envelope.get("receipt") if type(envelope) is dict else None
        if type(claim) is not dict:
            return False, "resource_signed_envelope_invalid"
        challenge = claim.get("challenge")
        if conn.execute(
            "SELECT 1 FROM spent_challenges WHERE challenge=?", (challenge,)
        ).fetchone() is not None:
            return False, "challenge_already_consumed"
        try:
            verdict, detail = _verifier().verify(
                envelope, pinned_public_keys=pinned_public_keys,
                pinned_key_epoch=current[3],
                independently_expected_effect=expected_effect,
                independently_held_anchor=prior,
                independently_issued_challenge=expected_challenge,
                readback=readback, now_ms=now_ms,
            )
        except Exception:
            return False, "resource_verification_unavailable"
        if not verdict:
            return False, detail
        receipt_digest = hashlib.sha256(_verifier().canonical(claim)).hexdigest()
        if conn.execute(
            "SELECT 1 FROM consumed_receipts WHERE receipt_digest=?", (receipt_digest,)
        ).fetchone() is not None:
            return False, "receipt_already_consumed"
        updated = conn.execute(
            "UPDATE highwater SET term=?,commit_seq=?,last_nonce=? "
            "WHERE resource_id='auto-router-runtime-projection' "
            "AND term=? AND commit_seq=? AND last_nonce=?",
            (claim["term"], claim["commit_seq"], claim["nonce"], *current[:3]),
        ).rowcount
        if updated != 1:
            return False, "anchor_raced_or_unavailable"
        conn.execute(
            "INSERT INTO spent_challenges VALUES (?,?,?,?)",
            (challenge, receipt_digest, claim["term"], claim["commit_seq"]),
        )
        conn.execute(
            "INSERT INTO consumed_receipts VALUES (?,?,?,?)",
            (receipt_digest, challenge, claim["term"], claim["commit_seq"]),
        )
        # A COMMIT API failure is an unknown outcome. It might have committed
        # and lost its acknowledgment. Returning an ordinary False would
        # invite unsafe retry of an already-applied effect.
        try:
            conn.commit()
        except sqlite3.Error as exc:
            raise UncertainReceiptCommit(
                "ANCHOR_COMMIT_ACK_UNCERTAIN_NO_RETRY"
            ) from exc
        did_commit = True
        if lose_ack_after_commit:
            raise UncertainReceiptCommit("ACK_UNOBSERVED_ANCHOR_COMMIT_OUTCOME_UNCERTAIN")
        return True, "RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY"
    except sqlite3.Error:
        return False, "anchor_sql_denied_or_uncertain"
    finally:
        if not did_commit:
            conn.rollback()
        conn.close()


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_REAL_ANCHOR_OR_RELEASE_ENTRYPOINT")
