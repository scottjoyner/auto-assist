#!/usr/bin/env python3
"""Disposable member COMMIT includes a whole pre-signed effect bundle.

The independent resource/anchor state and signing keys remain uncontrolled
by this SQLite transaction. Import-only research, no retry or delivery.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any


class UncertainOutboxCommit(Exception):
    """A successful SQLite commit may have lost its acknowledgment."""


@cache
def _load(filename: str) -> Any:
    p = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(
        "synthetic_outbox_" + filename.replace("-", "_").replace(".", "_"), p
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("research_source_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _member() -> Any:
    return _load("research-durable-witness-issuance.py")


def _verifier() -> Any:
    return _load("research-verify-signed-effect-bundle.py")


def _encoded(bundle: dict[str, Any]) -> str:
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def bootstrap(path: Path, witness_id: str) -> None:
    """Explicit schema bootstrap on a NEW disposable, empty witness ledger."""
    if not _member()._safe_path(path, witness_id) or not path.is_file():
        raise ValueError("outbox_disposable_scope_denied")
    with sqlite3.connect(f"{path.absolute().as_uri()}?mode=rw", uri=True) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        tip = db.execute(
            "SELECT commit_seq FROM tip WHERE witness_id=?", (witness_id,),
        ).fetchone()
        if tip != (11,) or db.execute("SELECT count(*) FROM issued").fetchone() != (0,):
            raise ValueError("outbox_requires_empty_research_witness")
        db.execute("""CREATE TABLE signed_bundle_outbox (
            commit_seq INTEGER PRIMARY KEY, challenge TEXT NOT NULL UNIQUE,
            bundle_sha256 TEXT NOT NULL UNIQUE, bundle_json TEXT NOT NULL)""")
        db.commit()


def issue(
    path: Path, *, witness_id: str, tip: Any, expected: Any,
    resource: Path, anchor: Path, artifact: Path, bundle: Any,
    effect_root: bytes, witness_root: bytes,
    challenge: str, now_ms: int,
    callback: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Verify complete bundle then COMMIT tip + issuance + bundle as one DB txn."""
    member = _member()
    verifier = _verifier()
    if not member._safe_path(path, witness_id) or not path.is_file():
        return False, "OUTBOX_DISPOSABLE_SCOPE_DENIED_NO_GO"
    if not member._quorum().valid_tip(tip) or type(bundle) is not dict:
        return False, "OUTBOX_INPUT_INVALID_NO_GO"
    encoded = _encoded(bundle)
    if len(encoded) > 32_768:
        return False, "OUTBOX_BUNDLE_TOO_LARGE_NO_GO"
    with sqlite3.connect(f"{path.absolute().as_uri()}?mode=rw", uri=True, timeout=2.5) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT term,commit_seq,head_hash FROM tip WHERE witness_id=?",
            (witness_id,),
        ).fetchone()
        if row is None or row[0] != tip["term"] or row[1] >= tip["commit_seq"]:
            return False, "OUTBOX_ALREADY_CONSUMED_OR_TERM_DENIED_NO_GO"
        if row[1] + 1 != tip["commit_seq"] or row[2] != tip["prev_hash"]:
            return False, "OUTBOX_NONADJACENT_OR_FORK_DENIED_NO_GO"
        member_cert = bundle.get("witness_certificate")
        if not isinstance(member_cert, dict) or member_cert.get("tip") != tip:
            return False, "OUTBOX_WITNESS_TIP_MISMATCH_NO_GO"
        verified = verifier.verify(
            resource, anchor, artifact, expected=expected, bundle=bundle,
            pinned_effect_root=effect_root, pinned_witness_root=witness_root,
            expected_witness_id=witness_id, expected_challenge=challenge,
            now_ms=now_ms,
        )
        if verified != (True, "RESEARCH_CRYPTOGRAPHIC_EFFECT_BUNDLE_ONLY_HOLD"):
            return False, "OUTBOX_SIGNED_EFFECT_VERIFICATION_DENIED_NO_GO"
        signature = member_cert["signature"]
        db.execute(
            "UPDATE tip SET commit_seq=?,last_nonce=?,receipt_digest=?,head_hash=? "
            "WHERE witness_id=? AND term=? AND commit_seq=? AND head_hash=?",
            (tip["commit_seq"], tip["last_nonce"], tip["receipt_digest"],
             tip["head_hash"], witness_id, row[0], row[1], row[2]),
        )
        if db.execute("SELECT changes()").fetchone() != (1,):
            return False, "OUTBOX_CAS_LOST_NO_GO"
        db.execute("INSERT INTO issued VALUES (?,?,?,?)", (
            tip["commit_seq"], tip["head_hash"], challenge, signature,
        ))
        db.execute("INSERT INTO signed_bundle_outbox VALUES (?,?,?,?)", (
            tip["commit_seq"], challenge,
            hashlib.sha256(encoded.encode("ascii")).hexdigest(), encoded,
        ))
        if callback is not None:
            callback("after_outbox_insert_before_commit")
        try:
            db.commit()
        except sqlite3.Error as exc:
            raise UncertainOutboxCommit("OUTBOX_COMMIT_ACK_UNCERTAIN_NO_RETRY") from exc
        if callback is not None:
            try:
                callback("after_commit_before_ack")
            except Exception as exc:
                raise UncertainOutboxCommit("OUTBOX_COMMITTED_ACK_UNCERTAIN_NO_RETRY") from exc
        return True, "RESEARCH_ATOMIC_OUTBOX_OBSERVED_HOLD"


def readback(path: Path, *, witness_id: str, seq: int) -> dict[str, Any]:
    """Inspect-only: no automatic delivery/repair/resigning/replay."""
    out: dict[str, Any] = {
        "release_authorized": False, "retry_authorized": False,
        "verdict": "UNKNOWN_NO_GO", "bundle": None,
    }
    if not _member()._safe_path(path, witness_id) or not path.is_file() or type(seq) is not int or seq <= 0:
        out["verdict"] = "OUTBOX_SCOPE_INVALID_NO_GO"
        return out
    try:
        with sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("sqlite_integrity")
            tip = db.execute(
                "SELECT commit_seq,head_hash FROM tip WHERE witness_id=?",
                (witness_id,),
            ).fetchone()
            issued = db.execute(
                "SELECT commit_seq,head_hash,challenge,signature FROM issued",
            ).fetchall()
            blobs = db.execute(
                "SELECT commit_seq,challenge,bundle_sha256,bundle_json FROM signed_bundle_outbox",
            ).fetchall()
        if tip is not None and tip[0] == seq - 1 and not issued and not blobs:
            out["verdict"] = "OUTBOX_NOT_COMMITTED_HOLD"
        elif tip is None or tip[0] != seq or len(issued) != 1 or len(blobs) != 1:
            out["verdict"] = "OUTBOX_LEDGER_CONTRADICTION_NO_GO"
        else:
            i, b = issued[0], blobs[0]
            if (
                i[0] != seq or b[0] != seq or i[1] != tip[1] or i[2] != b[1]
                or hashlib.sha256(b[3].encode("ascii")).hexdigest() != b[2]
            ):
                out["verdict"] = "OUTBOX_LEDGER_CONTRADICTION_NO_GO"
            else:
                bundle = json.loads(b[3])
                cert = bundle["witness_certificate"]
                if cert["signature"] != i[3] or cert["tip"]["head_hash"] != tip[1]:
                    out["verdict"] = "OUTBOX_CERTIFICATE_CONTRADICTION_NO_GO"
                else:
                    out["verdict"] = "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"
                    out["bundle"] = bundle
    except (OSError, sqlite3.Error, TypeError, KeyError, ValueError, UnicodeError):
        out["verdict"] = "OUTBOX_READBACK_UNAVAILABLE_OR_CORRUPT_NO_GO"
    return out


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_OUTBOX_DELIVERY_OR_RELEASE_ENTRYPOINT")
