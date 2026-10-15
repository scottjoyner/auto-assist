#!/usr/bin/env python3
"""Disposable witness local CAS/signing trial. NEVER production authority.

Signing private keys are passed transiently by pytest in memory, NOT stored in
SQLite or provided through an operational CLI. A SQLite local transaction can
serialize issuance; it cannot prove administrative independence, prevent host
superuser restore, or attest physical resource effects.
"""
from __future__ import annotations

import base64
import importlib.util
import sqlite3
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_IDS = ("a-owner", "b-archive", "c-reviewer")
_IDS_TO_NAME = {s: f"synthetic-{s}.sqlite" for s in _IDS}
_MARKER = ".synthetic-crash-lab-only"


class UncertainWitnessCommit(Exception):
    """Witness checkpoint may have committed despite a lost acknowledgment."""


@cache
def _quorum() -> Any:
    p = Path(__file__).with_name("research-quorum-highwater-tip.py")
    spec = importlib.util.spec_from_file_location("synthetic_quorum_witness", p)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic_quorum_unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _safe_path(path: Path, witness_id: str) -> bool:
    if (
        not isinstance(path, Path) or witness_id not in _IDS_TO_NAME
        or path.name != _IDS_TO_NAME[witness_id] or path.is_symlink()
    ):
        return False
    try:
        return (path.parent / _MARKER).read_text() == "DISPOSABLE_ONLY\n"
    except (OSError, UnicodeError):
        return False


def initialize(path: Path, *, witness_id: str, baseline: Any) -> None:
    """Only explicit disposable creation; never overwrite an existing ledger."""
    q = _quorum()
    if not _safe_path(path, witness_id) or path.exists() or not q.valid_baseline(baseline):
        raise ValueError("synthetic_witness_bootstrap_denied")
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE tip (
            witness_id TEXT PRIMARY KEY,
            term INTEGER NOT NULL, commit_seq INTEGER NOT NULL,
            last_nonce TEXT NOT NULL, receipt_digest TEXT NOT NULL,
            head_hash TEXT NOT NULL)""")
        db.execute("""CREATE TABLE issued (
            commit_seq INTEGER PRIMARY KEY, head_hash TEXT NOT NULL UNIQUE,
            challenge TEXT NOT NULL UNIQUE, signature TEXT NOT NULL)""")
        db.execute(
            "INSERT INTO tip VALUES(?,?,?,?,?,?)",
            (witness_id, baseline["term"], baseline["commit_seq"],
             baseline["last_nonce"], baseline["receipt_digest"], baseline["head_hash"]),
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def read_tip(path: Path, *, witness_id: str) -> dict[str, Any] | None:
    """Read-only fresh-process-style observer, not an anti-restore oracle."""
    if not _safe_path(path, witness_id):
        return None
    try:
        with sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.5
        ) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT term,commit_seq,last_nonce,receipt_digest,head_hash "
                "FROM tip WHERE witness_id=?", (witness_id,),
            ).fetchone()
        return (
            None if row is None
            else dict(zip(
                ("term", "commit_seq", "last_nonce", "receipt_digest", "head_hash"),
                row, strict=True,
            ))
        )
    except (sqlite3.Error, OSError):
        return None


def issue(
    path: Path, *, witness_id: str, tip: Any, expected: Any,
    resource: Path, anchor: Path, signer: Any, key_epoch: Any,
    challenge: Any, now_ms: Any,
    callback: Callable[[str], None] | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Single-use witness append and signed certificate under BEGIN IMMEDIATE.

    Matching mutable source/anchor SQL rows are not independently governed
    COMMIT attestation. Positive return is research-only, NOT release GO.
    """
    q = _quorum()
    local = q._local()
    if not _safe_path(path, witness_id) or not path.is_file():
        return False, "WITNESS_DISPOSABLE_SCOPE_DENIED", None
    if (
        not q.valid_tip(tip) or not local._valid_expected(expected)
        or not isinstance(signer, Ed25519PrivateKey)
        or type(key_epoch) is not int or key_epoch < 1
        or not q._sha(challenge) or type(now_ms) is not int or now_ms <= 0
    ):
        return False, "WITNESS_PROOF_INPUT_INVALID", None
    if (
        tip["resource_id"] != expected["resource_id"]
        or tip["term"] != expected["term"]
        or tip["commit_seq"] != expected["commit_seq"]
        or tip["last_nonce"] != expected["nonce"]
        or tip["receipt_digest"] != expected["receipt_digest"]
    ):
        return False, "WITNESS_EFFECT_DIGEST_MISMATCH", None
    if not local._safe_lab_paths(resource, anchor):
        return False, "WITNESS_LOCAL_RESOURCE_UNAVAILABLE", None
    try:
        db = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=rw", uri=True, timeout=2.5
        )
    except sqlite3.Error:
        return False, "WITNESS_LEDGER_UNAVAILABLE", None
    committed = False
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT term,commit_seq,last_nonce,receipt_digest,head_hash "
            "FROM tip WHERE witness_id=?", (witness_id,),
        ).fetchone()
        if row is None:
            return False, "WITNESS_ANCHOR_MISSING", None
        if row[0] != tip["term"]:
            return False, "WITNESS_TERM_CONFLICT", None
        if row[1] >= tip["commit_seq"]:
            return False, "WITNESS_STALE_OR_ALREADY_ISSUED", None
        if (
            tip["commit_seq"] != row[1] + 1
            or tip["prev_hash"] != row[4]
        ):
            return False, "WITNESS_NONADJACENT_OR_BROKEN_CHAIN", None
        before = local._read_snapshot(resource, anchor)
        after = local._read_snapshot(resource, anchor)
        if before is None or before != after:
            return False, "WITNESS_RESOURCE_READBACK_UNAVAILABLE_OR_CHANGED", None
        if local._classify(after, expected) != "ANCHOR_CONSUMED_OBSERVED_HOLD":
            return False, "WITNESS_REQUIRES_OBSERVED_ANCHOR_COMMIT", None

        cert = {
            "schema_version": 1, "witness_id": witness_id,
            "key_epoch": key_epoch, "challenge": challenge,
            "issued_at_ms": now_ms - 100,
            "expires_at_ms": now_ms + 10_000,
            "tip": tip,
        }
        sig = base64.b64encode(signer.sign(q.canonical(cert))).decode("ascii")
        signed = {**cert, "signature": sig}
        db.execute(
            "UPDATE tip SET commit_seq=?,last_nonce=?,receipt_digest=?,head_hash=? "
            "WHERE witness_id=? AND term=? AND commit_seq=? AND head_hash=?",
            (tip["commit_seq"], tip["last_nonce"], tip["receipt_digest"],
             tip["head_hash"], witness_id, row[0], row[1], row[4]),
        )
        if db.execute("SELECT changes()").fetchone() != (1,):
            return False, "WITNESS_CONCURRENT_CAS_DENIED", None
        db.execute(
            "INSERT INTO issued VALUES (?,?,?,?)",
            (tip["commit_seq"], tip["head_hash"], challenge, sig),
        )
        if callback is not None:
            callback("before_witness_commit")
        try:
            db.commit()
        except sqlite3.Error as exc:
            raise UncertainWitnessCommit("WITNESS_COMMIT_ACK_UNCERTAIN_NO_RETRY") from exc
        committed = True
        if callback is not None:
            try:
                callback("after_witness_commit_before_ack")
            except Exception as exc:
                raise UncertainWitnessCommit(
                    "WITNESS_COMMIT_ACK_UNCERTAIN_NO_RETRY"
                ) from exc
        return True, "RESEARCH_WITNESS_LOCALLY_ISSUED_HOLD", signed
    except sqlite3.IntegrityError:
        return False, "WITNESS_UNIQUE_ISSUANCE_CONFLICT", None
    finally:
        if not committed:
            db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_SIGNER_SERVICE_OR_PRODUCTION_ENTRYPOINT")
