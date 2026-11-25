"""Unwired private research journal for observation allocation and recording.

A producer must supply a stable, unique allocation key per attempted dispatch.
This journal cannot prove dispatch occurred or authenticate the producer.
Use a dedicated trusted local directory, not an active outbox or network FS.
No publication to the router, task graph, or any external service.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid
from contextlib import contextmanager, closing

from fleet_router_request_observation import (
    SCHEMA, InvalidObservation, observation_key, MAX_ROWS,
)

MAX_PAYLOAD_BYTES = 16384
MAX_ALLOCATION_KEY = 512
VERSION = 1


class JournalHold(InvalidObservation):
    """Fixed reason code; no source values."""


def _path(path, *, existing):
    path = Path(path).absolute()
    if path.parent.resolve(strict=True) != path.parent:
        raise JournalHold("untrusted_parent")
    parent = path.parent.stat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise JournalHold("private_directory_required")
    if existing:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise JournalHold("private_regular_file_required")
    return path


def initialize(path):
    """Exclusive creation; never adopt or truncate an existing file."""
    path = _path(path, existing=False)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    # A failed initialization remains on disk and cannot be silently recreated.
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE observation_journal (
            allocation_digest TEXT PRIMARY KEY,
            observation_id TEXT NOT NULL UNIQUE,
            payload_sha256 TEXT,
            payload_json TEXT,
            conflict INTEGER NOT NULL DEFAULT 0 CHECK(conflict IN (0,1)),
            CHECK ((payload_sha256 IS NULL) = (payload_json IS NULL))
        );
        PRAGMA user_version=1;
        COMMIT;
        """)
    # Persist directory entry on supporting local filesystems.
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _transaction(path):
    path = _path(path, existing=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=3,
                                 isolation_level=None)) as conn:
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA temp_store=MEMORY")
        if conn.execute("PRAGMA user_version").fetchone() != (VERSION,):
            raise JournalHold("unsupported_journal_version")
        if conn.execute("PRAGMA journal_mode").fetchone() != ("delete",):
            raise JournalHold("unsupported_journal_mode")
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def _allocation_digest(key):
    if type(key) is not str or not key.strip() or len(key) > MAX_ALLOCATION_KEY:
        raise JournalHold("invalid_allocation_key")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def reserve(path, allocation_key):
    """Return the same reserved identifier across reconnects for the same key."""
    digest = _allocation_digest(allocation_key)
    with _transaction(path) as conn:
        row = conn.execute(
            "SELECT observation_id,conflict FROM observation_journal WHERE allocation_digest=?",
            (digest,)).fetchone()
        if row:
            if row[1]:
                raise JournalHold("prior_observation_conflict")
            return row[0]
        if conn.execute("SELECT count(*) FROM observation_journal").fetchone()[0] >= MAX_ROWS:
            raise JournalHold("journal_row_limit")
        oid = "router-observation-" + uuid.uuid4().hex
        conn.execute("INSERT INTO observation_journal(allocation_digest,observation_id) VALUES (?,?)",
                     (digest, oid))
        return oid


def record(path, allocation_key, observation):
    """Persist first payload atomically. Conflict is sticky across sessions.

    Caller supplies builder output; this is not an arbitrary import validator.
    Return status only after transaction commit, including a conflict marker.
    """
    digest = _allocation_digest(allocation_key)
    if (type(observation) is not dict or observation.get("schema") != SCHEMA
            or observation.get("physical_execution_verified") is not False
            or observation.get("lineage_authenticated") is not False
            or observation.get("accepted_work_units", 0) is not None):
        raise JournalHold("not_builder_observation")
    observation_key(observation)
    raw = json.dumps(observation, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise JournalHold("observation_size_limit")
    payload_hash = hashlib.sha256(raw).hexdigest()
    with _transaction(path) as conn:
        row = conn.execute(
            "SELECT observation_id,payload_sha256,payload_json,conflict "
            "FROM observation_journal WHERE allocation_digest=?", (digest,)).fetchone()
        if row is None:
            raise JournalHold("allocation_missing")
        if row[0] != observation["observation_id"]:
            raise JournalHold("allocation_identity_mismatch")
        if row[3]:
            return "HOLD_PRIOR_CONFLICT"
        if row[1] is None:
            conn.execute("UPDATE observation_journal SET payload_sha256=?,payload_json=? "
                         "WHERE allocation_digest=?", (payload_hash, raw.decode("ascii"), digest))
            return "RECORDED"
        if row[1] == payload_hash and row[2] == raw.decode("ascii"):
            return "EXACT_REPLAY"
        conn.execute("UPDATE observation_journal SET conflict=1 WHERE allocation_digest=?",
                     (digest,))
        return "HOLD_CONFLICT"


def status(path):
    """Counts of local journal entries only; never an execution census."""
    with _transaction(path) as conn:
        total, pending, recorded, conflicts = conn.execute(
            "SELECT count(*),count(CASE WHEN payload_json IS NULL THEN 1 END),"
            "count(payload_json),coalesce(sum(conflict),0) FROM observation_journal"
        ).fetchone()
    return {"decision":"HOLD" if conflicts else "JOURNAL_ONLY",
            "reserved":total,"pending":pending,"recorded":recorded,
            "conflicted":conflicts,"actual_endpoint_calls":None,
            "verified_endpoint_receipts":None,"accepted_work_units":None}
