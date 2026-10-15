#!/usr/bin/env python3
"""Read-only terminal-state classifier for SYNTHETIC SQLite crash experiments.

Never releases work, advances an anchor, consumes a receipt, repairs state, or
retries an uncertain COMMIT. All outcomes are observational HOLD / NO-GO.
Fresh independent source/anchor reads are not a cross-DB atomic snapshot.
"""
from __future__ import annotations

import re
import sqlite3
import stat
from pathlib import Path
from typing import Any

_RESOURCE = "auto-router-runtime-projection"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_EXPECTED_FIELDS = {
    "resource_id", "term", "previous_seq", "commit_seq", "generation",
    "checksum", "revision_sha256", "nonce", "previous_nonce",
    "challenge", "receipt_digest",
}


def _valid_expected(x: Any) -> bool:
    return (
        type(x) is dict and set(x) == _EXPECTED_FIELDS
        and x["resource_id"] == _RESOURCE
        and all(type(x[k]) is int and 0 < x[k] < 2**63 for k in (
            "term", "previous_seq", "commit_seq", "generation"
        ))
        and x["commit_seq"] == x["previous_seq"] + 1
        and all(isinstance(x[k], str) and _HASH.fullmatch(x[k]) for k in (
            "checksum", "revision_sha256", "challenge", "receipt_digest"
        ))
        and all(isinstance(x[k], str) and _NONCE.fullmatch(x[k]) for k in (
            "nonce", "previous_nonce"
        ))
        and x["nonce"] != x["previous_nonce"]
    )


def _safe_lab_paths(resource: Path, anchor: Path) -> bool:
    if (
        not isinstance(resource, Path) or not isinstance(anchor, Path)
        or resource.name != "research-resource.sqlite"
        or anchor.name != "research-anchor.sqlite"
        or resource.parent != anchor.parent
        or resource == anchor
    ):
        return False
    marker = resource.parent / ".synthetic-crash-lab-only"
    try:
        for path in (marker, resource, anchor):
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                return False
        return marker.read_text(encoding="utf-8") == "DISPOSABLE_ONLY\n"
    except (OSError, ValueError):
        return False


def _read_snapshot(resource: Path, anchor: Path) -> tuple[Any, Any] | None:
    """Open both existing SQLite files as readonly, never bootstrap.

    Each database is internally consistent within its own BEGIN transaction.
    This does not provide a shared point-in-time transaction across the pair.
    """
    try:
        with sqlite3.connect(
            f"{resource.absolute().as_uri()}?mode=ro", uri=True, timeout=0.5
        ) as source:
            source.execute("PRAGMA query_only=ON")
            source.execute("BEGIN")
            if source.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                return None
            effect = source.execute(
                """SELECT resource_id,term,commit_seq,generation,checksum,
                          revision_sha256,committed FROM effect
                   WHERE resource_id=?""", (_RESOURCE,)
            ).fetchall()
            receipts = source.execute(
                "SELECT term,commit_seq,generation,checksum,revision_sha256,nonce "
                "FROM receipt"
            ).fetchall()
        with sqlite3.connect(
            f"{anchor.absolute().as_uri()}?mode=ro", uri=True, timeout=0.5
        ) as sink:
            sink.execute("PRAGMA query_only=ON")
            sink.execute("BEGIN")
            if sink.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                return None
            highwater = sink.execute(
                "SELECT term,commit_seq,last_nonce FROM highwater WHERE resource_id=?",
                (_RESOURCE,),
            ).fetchall()
            spent = sink.execute(
                "SELECT challenge,receipt_digest,term,commit_seq FROM spent_challenges"
            ).fetchall()
            consumed = sink.execute(
                "SELECT receipt_digest,challenge,term,commit_seq FROM consumed_receipts"
            ).fetchall()
    except (OSError, ValueError, sqlite3.Error):
        return None
    return (effect, receipts), (highwater, spent, consumed)


def _classify(snapshot: tuple[Any, Any], expect: dict[str, Any]) -> str:
    (effects, receipts), (highwater, spent, consumed) = snapshot
    if len(highwater) != 1 or len(effects) > 1 or len(receipts) > 1:
        return "CONTRADICTION_NO_GO"
    expected_effect = (
        _RESOURCE, expect["term"], expect["commit_seq"], expect["generation"],
        expect["checksum"], expect["revision_sha256"], 1,
    )
    expected_receipt = (
        expect["term"], expect["commit_seq"], expect["generation"],
        expect["checksum"], expect["revision_sha256"], expect["nonce"],
    )
    if (effects or receipts) and (effects != [expected_effect] or receipts != [expected_receipt]):
        return "RESOURCE_MISMATCH_NO_GO"
    highwater_prev = (expect["term"], expect["previous_seq"], expect["previous_nonce"])
    highwater_new = (expect["term"], expect["commit_seq"], expect["nonce"])
    expected_spent = (
        expect["challenge"], expect["receipt_digest"],
        expect["term"], expect["commit_seq"],
    )
    expected_consumed = (
        expect["receipt_digest"], expect["challenge"],
        expect["term"], expect["commit_seq"],
    )
    if highwater == [highwater_prev] and not spent and not consumed:
        return (
            "SOURCE_COMMITTED_ANCHOR_PENDING_HOLD"
            if effects else "SOURCE_NOT_OBSERVED_HOLD"
        )
    if (
        highwater == [highwater_new]
        and effects == [expected_effect]
        and spent == [expected_spent]
        and consumed == [expected_consumed]
    ):
        return "ANCHOR_CONSUMED_OBSERVED_HOLD"
    return "ANCHOR_OR_LEDGER_CONTRADICTION_NO_GO"


def assess(
    resource: Path, anchor: Path, *, expected: Any,
) -> dict[str, Any]:
    """Repeat read-only snapshots; classify observation, NEVER approve retry."""
    report: dict[str, Any] = {
        "schema_version": 1,
        "release_authorized": False,
        "retry_authorized": False,
        "verdict": "UNKNOWN_NO_GO",
    }
    if not _valid_expected(expected):
        report["verdict"] = "EXPECTED_EVIDENCE_INVALID_NO_GO"
        return report
    if not _safe_lab_paths(resource, anchor):
        report["verdict"] = "DISPOSABLE_SCOPE_INVALID_NO_GO"
        return report
    first = _read_snapshot(resource, anchor)
    second = _read_snapshot(resource, anchor)
    if first is None or second is None:
        report["verdict"] = "UNAVAILABLE_OR_UNTRUSTED_READBACK_NO_GO"
    elif first != second:
        report["verdict"] = "CHANGED_BETWEEN_READBACKS_NO_GO"
    else:
        report["verdict"] = _classify(second, expected)
    return report


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_TERMINAL_AUTHORITY_OR_RETRY_ENTRYPOINT")
