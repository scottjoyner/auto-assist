#!/usr/bin/env python3
"""Disposable effect-time owner term fence; NOT an independent resource owner.

Reverify #397's recovered bundle, then compare exact owner/term/epoch INSIDE
the same local SQLite transaction as a tiny synthetic effect INSERT.
No production dispatch, network, automatic retry, or release authorization.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

_EFFECT_DB = "synthetic-fenced-effects.sqlite"
_RESOURCE = "auto-router-runtime-projection"
_OWNER = "owner-a"


class UncertainSyntheticEffectCommit(Exception):
    """The local effect may have committed; NEVER replay on missing ACK."""


@cache
def _recovery() -> Any:
    path = Path(__file__).with_name("research-verify-recovered-effect-outbox.py")
    spec = importlib.util.spec_from_file_location("synthetic_effect_time_recovery", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("disposable_recovery_verifier_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_path(path: Path) -> bool:
    if (
        not isinstance(path, Path)
        or path.name != _EFFECT_DB
        or path.is_symlink()
    ):
        return False
    try:
        return (
            (path.parent / ".synthetic-crash-lab-only").read_text()
            == "DISPOSABLE_ONLY\n"
        )
    except (OSError, UnicodeError):
        return False


def bootstrap(path: Path) -> None:
    """Explicitly create an empty synthetic effect journal, never overwrite."""
    if not _safe_path(path) or path.exists():
        raise ValueError("synthetic_effect_bootstrap_denied")
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE authority (
            resource_id TEXT PRIMARY KEY,
            term INTEGER NOT NULL, epoch INTEGER NOT NULL,
            owner_id TEXT NOT NULL, revoked INTEGER NOT NULL)""")
        db.execute("""CREATE TABLE applied (
            commit_seq INTEGER PRIMARY KEY, term INTEGER NOT NULL,
            epoch INTEGER NOT NULL, owner_id TEXT NOT NULL,
            nonce TEXT NOT NULL UNIQUE, challenge TEXT NOT NULL UNIQUE,
            artifact_sha256 TEXT NOT NULL, bundle_sha256 TEXT NOT NULL)""")
        db.execute(
            "INSERT INTO authority VALUES(?,?,?,?,?)",
            (_RESOURCE, 4, 9, _OWNER, 0),
        )
        db.commit()
    finally:
        db.close()


def rotate(
    path: Path, *, term: int, epoch: int, owner: str, revoked: bool = False,
) -> bool:
    """Simulated revocation under local transaction; same OS admin controls it."""
    if (
        not _safe_path(path) or not path.is_file()
        or type(term) is not int or type(epoch) is not int
        or term < 1 or epoch < 1 or not isinstance(owner, str) or not owner
    ):
        return False
    try:
        with sqlite3.connect(
            f"{path.absolute().as_uri()}?mode=rw", uri=True, timeout=0.1
        ) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT term,epoch FROM authority WHERE resource_id=?",
                (_RESOURCE,),
            ).fetchone()
            if current is None or (
                term < current[0] or
                (term == current[0] and epoch <= current[1])
            ):
                return False
            db.execute(
                "UPDATE authority SET term=?,epoch=?,owner_id=?,revoked=? "
                "WHERE resource_id=?",
                (term, epoch, owner, int(revoked), _RESOURCE),
            )
            return True
    except sqlite3.Error:
        return False


def execute_once(
    effects: Path, member: Path, resource: Path, anchor: Path, artifact: Path,
    *, expected: dict[str, Any], bundle_sha256: str,
    previous_head_hash: str, effect_root: bytes, witness_root: bytes,
    challenge: str, now_ms: int, required_owner: str = _OWNER,
    required_epoch: int = 9,
    after_verify: Callable[[], None] | None = None,
    during_effect_lock: Callable[[], None] | None = None,
    before_effect_commit: Callable[[], None] | None = None,
    after_commit: Callable[[], None] | None = None,
) -> tuple[bool, str]:
    """Local effect-time CAS: verification alone CANNOT authorize production."""
    if (
        not _safe_path(effects) or not effects.is_file()
        or not isinstance(required_owner, str) or required_owner != _OWNER
        or type(required_epoch) is not int or required_epoch < 1
    ):
        return False, "DISPOSABLE_EXECUTOR_OR_OWNER_DENIED_NO_GO"
    observation = _recovery().inspect(
        member, witness_id="a-owner", seq=expected.get("commit_seq", -1),
        resource=resource, anchor=anchor, artifact=artifact,
        expected=expected, pinned_bundle_sha256=bundle_sha256,
        pinned_previous_head_hash=previous_head_hash,
        pinned_effect_root=effect_root, pinned_witness_root=witness_root,
        challenge=challenge, now_ms=now_ms,
    )
    if observation["verdict"] != "RECOVERY_CROSS_SIGNED_BUNDLE_OBSERVED_HOLD":
        return False, "RECOVERED_RECEIPT_NOT_AUTHENTICATED_NO_GO"
    # Deliberately a window here: it MUST be closed by the effect-time fence,
    # not by trusting a stale readback performed above.
    if after_verify is not None:
        after_verify()
    try:
        db = sqlite3.connect(
            f"{effects.absolute().as_uri()}?mode=rw", uri=True, timeout=0.15,
        )
    except sqlite3.Error:
        return False, "EFFECT_JOURNAL_UNAVAILABLE_NO_GO"
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        if during_effect_lock is not None:
            during_effect_lock()
        row = db.execute(
            "SELECT term,epoch,owner_id,revoked FROM authority WHERE resource_id=?",
            (_RESOURCE,),
        ).fetchone()
        if row != (expected["term"], required_epoch, required_owner, 0):
            return False, "STALE_OR_REVOKED_OWNER_AT_EFFECT_TIME_NO_GO"
        # Repeat the *cryptographic* resource/anchor/artifact verification
        # at effect time, not merely the outbox's local self-hash check.
        # This is NOT a cross-resource lock or protected execution boundary.
        final = _recovery().inspect(
            member, witness_id="a-owner", seq=expected["commit_seq"],
            resource=resource, anchor=anchor, artifact=artifact,
            expected=expected, pinned_bundle_sha256=bundle_sha256,
            pinned_previous_head_hash=previous_head_hash,
            pinned_effect_root=effect_root, pinned_witness_root=witness_root,
            challenge=challenge, now_ms=now_ms,
        )
        if final["verdict"] != "RECOVERY_CROSS_SIGNED_BUNDLE_OBSERVED_HOLD":
            return False, "EVIDENCE_CHANGED_AT_EFFECT_TIME_NO_GO"
        # Expected digest is already checked by the public-key verifier.
        # No private keys, model dispatch, or actual resource execution.
        bundle = _recovery()._source(
            "research-atomic-effect-bundle-outbox.py"
        ).readback(member, witness_id="a-owner", seq=expected["commit_seq"]).get("bundle")
        if not isinstance(bundle, dict):
            return False, "BUNDLE_DISAPPEARED_BEFORE_EFFECT_NO_GO"
        artifact_hash = bundle["effect_attestation"]["claim"]["artifact_sha256"]
        db.execute(
            "INSERT INTO applied VALUES(?,?,?,?,?,?,?,?)",
            (expected["commit_seq"], expected["term"], required_epoch,
             required_owner, expected["nonce"], challenge, artifact_hash,
             bundle_sha256),
        )
        if before_effect_commit is not None:
            before_effect_commit()
        try:
            db.commit()
        except sqlite3.Error as exc:
            raise UncertainSyntheticEffectCommit(
                "SYNTHETIC_EFFECT_COMMIT_UNCERTAIN_NO_RETRY"
            ) from exc
        if after_commit is not None:
            try:
                after_commit()
            except Exception as exc:
                raise UncertainSyntheticEffectCommit(
                    "SYNTHETIC_EFFECT_ACK_UNCERTAIN_NO_RETRY"
                ) from exc
        return True, "RESEARCH_LOCAL_EFFECT_TIME_FENCE_ONLY_HOLD"
    except sqlite3.IntegrityError:
        return False, "EFFECT_ALREADY_APPLIED_NO_RETRY"
    finally:
        db.rollback()
        db.close()


def inspect_effect(path: Path) -> dict[str, Any]:
    """Read-only local effect count; never a real physical-effect witness."""
    result = {
        "release_authorized": False, "retry_authorized": False,
        "verdict": "UNKNOWN_NO_GO", "count": None,
    }
    if not _safe_path(path) or not path.is_file():
        result["verdict"] = "DISPOSABLE_EFFECT_SCOPE_INVALID_NO_GO"
        return result
    try:
        with sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("integrity")
            count = db.execute("SELECT count(*) FROM applied").fetchone()[0]
        result["verdict"] = "SYNTHETIC_EFFECT_OBSERVED_HOLD"
        result["count"] = count
    except (sqlite3.Error, ValueError, OSError):
        result["verdict"] = "EFFECT_READBACK_UNAVAILABLE_NO_GO"
    return result


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_REAL_EFFECT_OR_RELEASE_ENTRYPOINT")
