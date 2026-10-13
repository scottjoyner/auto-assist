"""Offline P0 approval ledger countermeasure; RESEARCH ONLY, NO GRAPH WRITES.

Separately pinned source/reviewer public keys and signed manifest are validated
before reserving an approval ID in an isolated SQLite ledger. Each reservation
is *uncertain* until a separately trusted graph commit witness exists. This
module deliberately provides NO graph executor or auto-complete/reset method.
It cannot enforce production approval against clients with Neo4j admin grants.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.runtime_projection_signed_apply_gate import validate

OPT_IN = "ASSISTX_P0_LEDGER_RESEARCH"


class AdmissionHold(ValueError):
    pass


@dataclass(frozen=True)
class QuarantinedAttempt:
    generation: int
    status: str = "PENDING_UNCERTAIN_GRAPH_OUTCOME"
    graph_write_authorized: bool = False
    production_admission_authorized: bool = False


def _connect(db_path: Path) -> sqlite3.Connection:
    if os.environ.get(OPT_IN) != "1":
        raise AdmissionHold("EXPLICIT_RESEARCH_OPT_IN_REQUIRED")
    if (not isinstance(db_path, Path)
            or db_path.is_symlink()
            or not db_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
            or db_path.suffix != ".sqlite3"):
        raise AdmissionHold("DISPOSABLE_LEDGER_PATH_REQUIRED")
    # Only create a disposable mode-0600 file; reject shared/readable copies.
    # This offline fixture is not a production trust root or recovery store.
    if not db_path.exists():
        try:
            fd = os.open(db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    if (not db_path.is_file() or db_path.stat().st_mode & 0o077):
        raise AdmissionHold("PRIVATE_DISPOSABLE_LEDGER_REQUIRED")
    db = sqlite3.connect(db_path, timeout=3, isolation_level=None)
    db.execute("PRAGMA busy_timeout=3000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize(db_path: Path, *, trusted_genesis_generation: int) -> None:
    if type(trusted_genesis_generation) is not int or trusted_genesis_generation < 0:
        raise AdmissionHold("TRUSTED_GENESIS_REQUIRED")
    with _connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS custody ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
            "last_confirmed_generation INTEGER NOT NULL, "
            "pending_approval_id TEXT, pending_generation INTEGER)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS replay (approval_id TEXT PRIMARY KEY, "
            "generation INTEGER NOT NULL, source_sha256 TEXT NOT NULL, "
            "state TEXT NOT NULL CHECK(state='PENDING_UNCERTAIN_GRAPH_OUTCOME'))")
        state = db.execute(
            "SELECT last_confirmed_generation FROM custody WHERE singleton=1"
        ).fetchone()
        if state is None:
            db.execute(
                "INSERT INTO custody(singleton,last_confirmed_generation) VALUES (1,?)",
                (trusted_genesis_generation,))
        elif state[0] != trusted_genesis_generation:
            raise AdmissionHold("GENESIS_REBIND_OR_ROLLBACK_DENIED")
        db.execute("COMMIT")


def reserve_verified_intent(
    db_path: Path, *, manifest: dict, attestation: dict,
    pinned_source_public_key: bytes, pinned_reviewer_public_key: bytes,
    observed_now_ms: int,
) -> QuarantinedAttempt:
    # Do not run any graph or DB side effect before independent cryptography.
    proof = validate(
        manifest, attestation, pinned_source_public_key,
        pinned_reviewer_public_key, observed_now_ms)
    expected = manifest.get("expected_current_generation")
    if type(expected) is not int or proof.generation != expected + 1:
        raise AdmissionHold("NONMONOTONIC_GENERATION_DENIED")
    if not 30 <= manifest.get("ttl_seconds", -1) <= 900:
        raise AdmissionHold("TTL_OUTSIDE_GOVERNED_BOUND")
    with _connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        state = db.execute(
            "SELECT last_confirmed_generation,pending_approval_id "
            "FROM custody WHERE singleton=1"
        ).fetchone()
        if state is None:
            raise AdmissionHold("NO_ATTESTED_GENESIS")
        last, pending = state
        if pending is not None:
            raise AdmissionHold("UNRESOLVED_INFLIGHT_ATTEMPT_HOLD")
        if last != expected:
            raise AdmissionHold("DURABLE_GENERATION_HIGH_WATER_DENIED")
        try:
            db.execute(
                "INSERT INTO replay(approval_id,generation,source_sha256,state)"
                " VALUES(?,?,?,'PENDING_UNCERTAIN_GRAPH_OUTCOME')",
                (proof.approval_id, proof.generation, proof.source_sha256))
        except sqlite3.IntegrityError:
            raise AdmissionHold("APPROVAL_ID_REPLAY_DENIED") from None
        db.execute(
            "UPDATE custody SET pending_approval_id=?, pending_generation=? "
            "WHERE singleton=1 AND last_confirmed_generation=? "
            "AND pending_approval_id IS NULL",
            (proof.approval_id, proof.generation, expected))
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise AdmissionHold("CAS_RESERVATION_DENIED")
        db.execute("COMMIT")
    return QuarantinedAttempt(generation=proof.generation)


def inspection(db_path: Path) -> dict:
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT last_confirmed_generation,pending_approval_id,pending_generation "
            "FROM custody WHERE singleton=1").fetchone()
        if row is None:
            raise AdmissionHold("NO_ATTESTED_GENESIS")
        count = db.execute("SELECT count(*) FROM replay").fetchone()[0]
    return {
        "last_confirmed_generation": row[0],
        "unresolved_pending": row[1] is not None,
        "pending_generation": row[2],
        "replay_tombstone_count": count,
        "graph_write_authorized": False,
        "auto_reconciliation_authorized": False,
    }


def complete_without_independent_graph_witness(*_args: object, **_kwargs: object) -> None:
    raise AdmissionHold("NO_INDEPENDENT_GRAPH_COMMIT_PROOF_PERSIST_HOLD")
