#!/usr/bin/env python3
"""SQLite-only, disposable resource-COMMIT experiment. NO production authority.

This is an import-only, local test instrument. No network, environment secrets,
database service, release CLI, automatic retry, or background job is used.
Separate SQLite files model the resource effect and a *simulated* external
high-water. Separate files are NOT independent governance: the same OS user
can modify both, including with privileged SQL.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_RESOURCE = "auto-router-runtime-projection"
_SHA = "a" * 64
_REV = "b" * 64
_NONCE0 = "f" * 32


class RejectedCommit(Exception):
    """Failed the resource-owned, compare-and-swap transaction."""


class UncertainCommit(Exception):
    """Commit may have succeeded, but the client did not receive its ACK."""


def _conn(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    # Only called on disposable temp paths in tests. URI mode=ro ensures a
    # separate observation connection cannot issue ordinary SQL writes.
    if readonly:
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        con.execute("PRAGMA query_only=ON")
    else:
        con = sqlite3.connect(path)
        con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA busy_timeout=1000")
    return con


def initialize(resource_db: Path, anchor_db: Path) -> None:
    if resource_db.resolve() == anchor_db.resolve():
        raise ValueError("resource and high-water must use separate disposable files")
    for db in (resource_db, anchor_db):
        if db.exists():
            raise ValueError("research bootstrap refuses to overwrite any database")
        if not db.parent.is_dir():
            raise ValueError("research databases must live under existing test temp dirs")
    with _conn(resource_db) as db:
        db.execute("""CREATE TABLE resource_state (
            id TEXT PRIMARY KEY, term INTEGER NOT NULL, commit_seq INTEGER NOT NULL,
            generation INTEGER NOT NULL, checksum TEXT NOT NULL,
            revision_sha256 TEXT NOT NULL, nonce TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE commit_receipts (
            seq INTEGER PRIMARY KEY, term INTEGER NOT NULL,
            nonce TEXT NOT NULL UNIQUE, generation INTEGER NOT NULL,
            checksum TEXT NOT NULL, revision_sha256 TEXT NOT NULL
        )""")
        db.execute("INSERT INTO resource_state VALUES (?,?,?,?,?,?,?)",
                   (_RESOURCE, 1, 1, 1, _SHA, _REV, _NONCE0))
        db.execute("INSERT INTO commit_receipts VALUES (?,?,?,?,?,?)",
                   (1, 1, _NONCE0, 1, _SHA, _REV))
    with _conn(anchor_db) as db:
        db.execute("""CREATE TABLE witness_highwater (
            id TEXT PRIMARY KEY, term INTEGER NOT NULL,
            commit_seq INTEGER NOT NULL, last_nonce TEXT NOT NULL
        )""")
        db.execute("INSERT INTO witness_highwater VALUES (?,?,?,?)",
                   (_RESOURCE, 1, 1, _NONCE0))


def commit_effect(
    resource_db: Path, *,
    expected_term: int, expected_sequence: int,
    generation: int, checksum: str, revision_sha256: str, nonce: str,
    lose_ack_after_commit: bool = False,
) -> None:
    """Atomically commit an effect and a receipt, then optionally lose the ACK.

    No caller can *legitimately* advance a term or sequence through this API
    without its expected CAS inputs. A privileged SQL writer can still forge
    both tables, a deliberately reproduced NO-GO below.
    """
    if (
        type(expected_term) is not int or expected_term < 1
        or type(expected_sequence) is not int or expected_sequence < 1
        or type(generation) is not int or generation < 1
        or not isinstance(checksum, str) or len(checksum) != 64
        or not isinstance(revision_sha256, str) or len(revision_sha256) != 64
        or not isinstance(nonce, str) or len(nonce) != 32
    ):
        raise RejectedCommit("invalid_commit_shape")
    con = _conn(resource_db)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT term, commit_seq FROM resource_state WHERE id=?", (_RESOURCE,)
        ).fetchone()
        if row != (expected_term, expected_sequence):
            raise RejectedCommit("stale_term_or_sequence")
        next_seq = expected_sequence + 1
        con.execute("""UPDATE resource_state SET term=?, commit_seq=?,
            generation=?, checksum=?, revision_sha256=?, nonce=? WHERE id=?""",
            (expected_term, next_seq, generation, checksum, revision_sha256,
             nonce, _RESOURCE))
        con.execute("INSERT INTO commit_receipts VALUES (?,?,?,?,?,?)",
                    (next_seq, expected_term, nonce, generation, checksum,
                     revision_sha256))
        con.commit()
    except sqlite3.IntegrityError as exc:
        con.rollback()
        raise RejectedCommit("duplicate_nonce_or_sequence") from exc
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    if lose_ack_after_commit:
        raise UncertainCommit("ACK_LOST_AFTER_DURABLE_COMMIT_DO_NOT_RETRY")


def resource_readback(resource_db: Path) -> dict[str, Any]:
    """Separate read-only SQL connection; no signer / external state involved."""
    with _conn(resource_db, readonly=True) as db:
        state = db.execute("""SELECT term, commit_seq, generation,
            checksum, revision_sha256, nonce FROM resource_state
            WHERE id=?""", (_RESOURCE,)).fetchone()
        if state is None:
            raise ValueError("missing_resource")
        term, seq, generation, checksum, revision, nonce = state
        receipt = db.execute("""SELECT term, nonce, generation, checksum,
            revision_sha256 FROM commit_receipts WHERE seq=?""", (seq,)).fetchone()
        committed = receipt == (term, nonce, generation, checksum, revision)
    return {
        "resource_id": _RESOURCE, "term": term, "commit_seq": seq,
        "generation": generation, "checksum": checksum,
        "revision_sha256": revision, "committed": committed,
    }


def anchor_readback(anchor_db: Path) -> dict[str, Any]:
    with _conn(anchor_db, readonly=True) as db:
        row = db.execute(
            "SELECT term, commit_seq, last_nonce FROM witness_highwater WHERE id=?",
            (_RESOURCE,),
        ).fetchone()
        if row is None:
            raise ValueError("missing_external_anchor")
        return dict(zip(("term", "commit_seq", "last_nonce"), row, strict=True))


def operator_advance_anchor(
    anchor_db: Path, *, expected_term: int, expected_seq: int,
    new_term: int, new_seq: int, new_nonce: str,
) -> None:
    """Separate manual research action; NEVER called by witness evaluation."""
    with _conn(anchor_db) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute(
            "SELECT term, commit_seq FROM witness_highwater WHERE id=?",
            (_RESOURCE,),
        ).fetchone()
        if old != (expected_term, expected_seq) or (
            new_term, new_seq
        ) <= (expected_term, expected_seq):
            raise RejectedCommit("anchor_moved_or_not_monotonic")
        db.execute("""UPDATE witness_highwater
            SET term=?, commit_seq=?, last_nonce=? WHERE id=?""",
            (new_term, new_seq, new_nonce, _RESOURCE))


def automatic_retry_after_uncertain_commit(*_args: Any, **_kwargs: Any) -> None:
    """No-automatic-retry guard; unsure whether the resource effect happened."""
    raise UncertainCommit("UNCERTAIN_COMMIT_REQUIRES_INDEPENDENT_OPERATOR_READBACK")


if __name__ == "__main__":
    raise SystemExit("DISPOSABLE_RESEARCH_ONLY_NO_PRODUCTION_ENTRYPOINT")
