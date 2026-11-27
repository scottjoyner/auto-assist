"""Offline concurrency limits of the real AssistX outbox claim path (r19).

Question: how many parallel claimants can safely use the same outbox?

This module exercises the *production* ``OutboxClient._claim_next()`` with a
fresh file-backed SQLite database under pytest ``tmp_path`` (which lives under
the SSD scratch ``TMPDIR``). There is no live service, no HTTP delivery, no
credential, no live DB and no production-code change. The only harness knobs
are:

* ``sqlite3.connect`` busy timeout is shortened through a module-local shim so
  a stuck write lock fails fast instead of waiting the production 10 s;
* a long claim lease keeps seeded rows from expiring mid-test;
* worker threads/processes and their joins are bounded by short timeouts.

The tests below are observations for this harness and this host, not a universal
claimant-count guarantee. See the module docstring limits and the r19 report.
"""

import json
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

import assistx.outbox_client as oc
from assistx.outbox_client import OutboxClient

# Verbatim R4 reconciliation hold label (round4-session1 report, SQL UPDATE at
# line 114; the R18 test pins the same string to the production claim guard).
R4_MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
PAYLOAD = '{"note":"PAYLOAD_SENTINEL_NEVER_PRINTED"}'

N_CLAIMANTS = (4, 8)
N_PENDING = 24
N_FAILED = 24
CLAIMABLE_COUNT = N_PENDING + N_FAILED
BUSY_TIMEOUT_S = 2.0
LOCK_BUSY_TIMEOUT_S = 0.5
JOIN_TIMEOUT_S = 15.0
PROC_TIMEOUT_S = 30.0
LEASE_S = 3600  # harness-only: no claim can expire during a sub-second test
STALE_CLAIMED_AT = "2026-11-26T00:00:00+00:00"

# A child process runs the same production claim loop, with no in-process
# fixture or patched module. PYTHONPATH (set by tests/conftest.py) and the SSD
# scratch cache env vars are inherited from the parent.
PROCESS_WORKER = r"""
import json, os, sys
from assistx.outbox_client import OutboxClient
client = OutboxClient(db_path=sys.argv[1], api_url="", auto_flush=False, claim_lease_s=3600)
claims = []
while True:
    entry = client._claim_next()
    if entry is None:
        break
    claims.append([entry.outbox_id, entry.claim_owner, entry.claimed_at])
print(json.dumps(claims))
"""


class ShortBusyTimeoutSqlite:
    """sqlite3 facade for oc only: shorten connect timeout, keep real SQLite."""

    Error = sqlite3.Error
    Connection = sqlite3.Connection
    timeout_s = BUSY_TIMEOUT_S

    @staticmethod
    def connect(database, *args, **kwargs):
        kwargs["timeout"] = ShortBusyTimeoutSqlite.timeout_s
        return sqlite3.connect(database, *args, **kwargs)


@pytest.fixture
def outbox_db(tmp_path):
    """A fresh file DB on the SSD: claimable rows plus two quarantine holds."""
    db_path = tmp_path / "concurrent-claims.sqlite"
    assert not db_path.exists()

    # Let production create and migrate the schema; no hand-copied DDL.
    OutboxClient(db_path=str(db_path), api_url="", auto_flush=False)

    rows = []
    for i in range(N_PENDING):
        rows.append(
            (
                f"pending-{i:02d}",
                f"evt-pending-{i:02d}",
                "pending",
                f"2026-11-26T01:00:{i:02d}+00:00",
                0,
                None,
                None,
            )
        )
    for i in range(N_FAILED):
        rows.append(
            (
                f"failed-{i:02d}",
                f"evt-failed-{i:02d}",
                "failed",
                f"2026-11-26T02:00:{i:02d}+00:00",
                1,
                None,
                None,
            )
        )
    # Both markers are the *oldest* rows. One is pending, one is a stale
    # 'claimed' lease; without the guard both would be first in line to claim.
    rows.append(("held-pending", "evt-held-pending", "pending", "2026-11-26T00:00:00+00:00", 9, R4_MARKER, None))
    rows.append(
        (
            "held-stale",
            "evt-held-stale",
            "claimed",
            "2026-11-26T00:00:01+00:00",
            0,
            R4_MARKER,
            STALE_CLAIMED_AT,
        )
    )

    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    try:
        conn.executemany(
            "INSERT INTO outbox (outbox_id, event_id, payload_json, status, created_at, "
            "attempt_count, claim_owner, claimed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (oid, event, PAYLOAD, status, created, attempt, owner, claimed)
                for oid, event, status, created, attempt, owner, claimed in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()

    expected = {row[0] for row in rows if row[5] != R4_MARKER}
    markers = {"held-pending", "held-stale"}
    assert len(expected) == CLAIMABLE_COUNT
    assert not expected & markers
    return db_path, expected, markers


@pytest.fixture
def short_busy_timeout(monkeypatch):
    """Shorten the production module's SQLite busy timeout, nothing else."""
    shim = ShortBusyTimeoutSqlite
    shim.timeout_s = BUSY_TIMEOUT_S
    monkeypatch.setattr(oc, "sqlite3", shim)
    return shim


def _snapshot(db_path):
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    try:
        return {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute("SELECT outbox_id, status, claim_owner, claimed_at FROM outbox")
        }
    finally:
        conn.close()


def _assert_claim_invariants(claims, errors, expected, markers, db_path):
    """Shared assertions: no double claim, exact total, guard holds in the DB."""
    assert not errors, errors
    ids = [item[0] for item in claims]
    assert len(ids) == CLAIMABLE_COUNT
    assert len(set(ids)) == CLAIMABLE_COUNT, "a row was claimed by two claimants"
    assert set(ids) == expected
    assert not set(ids) & markers, "a quarantine marker was claimed"
    tokens = [item[1] for item in claims]
    assert all(tokens)
    assert len(set(tokens)) == len(tokens), "two claims shared one owner token"
    assert all(item[2] for item in claims), "a claim has no claimed_at"

    final = _snapshot(db_path)
    for item in claims:
        oid, token, claimed_at = item[:3]
        assert final[oid] == ("claimed", token, claimed_at)
    assert final["held-pending"] == ("pending", R4_MARKER, None)
    assert final["held-stale"] == ("claimed", R4_MARKER, STALE_CLAIMED_AT)
    assert sum(1 for row in final.values() if row[0] == "claimed") == CLAIMABLE_COUNT + 1


@pytest.mark.parametrize("n", N_CLAIMANTS)
def test_parallel_threads_claim_each_row_once(n, outbox_db, short_busy_timeout):
    """N=4 and N=8 thread claimants: one row each, markers always skipped."""
    db_path, expected, markers = outbox_db
    assert "LIKE 'quarantine:%'" in oc._QUARANTINE_CLAIM_GUARD

    gate = threading.Barrier(n)
    outcomes = [None] * n

    def worker(index):
        # One OutboxClient per claimant: the process-local lock in each client
        # cannot serialize the others, so only SQLite can fence the claims.
        client = OutboxClient(db_path=str(db_path), api_url="", auto_flush=False, claim_lease_s=LEASE_S)
        claims, errors = [], []
        gate.wait(timeout=JOIN_TIMEOUT_S)
        for _ in range(CLAIMABLE_COUNT + 2):
            try:
                entry = client._claim_next()
            except sqlite3.Error as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                break
            if entry is None:
                break
            claims.append((entry.outbox_id, entry.claim_owner, entry.claimed_at, index))
            if len(claims) > CLAIMABLE_COUNT:
                errors.append("claim loop exceeded the seeded row count")
                break
        return claims, errors

    def run(index):
        try:
            outcomes[index] = worker(index)
        except BaseException as exc:  # surface a harness failure, not a hang
            outcomes[index] = ([], [f"worker-{index} {type(exc).__name__}: {exc}"])

    threads = [threading.Thread(target=run, args=(i,), name=f"claimant-{i}") for i in range(n)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + JOIN_TIMEOUT_S
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    assert not any(thread.is_alive() for thread in threads), "claimant did not finish"
    assert all(outcome is not None for outcome in outcomes)

    claims = [claim for claims, _ in outcomes for claim in claims]
    errors = [error for _, errors in outcomes for error in errors]
    _assert_claim_invariants(claims, errors, expected, markers, db_path)


def test_external_write_lock_is_busy_then_claims_once(outbox_db, short_busy_timeout):
    """A held SQLite write lock makes _claim_next fail busy, not double-claim."""
    db_path, expected, _markers = outbox_db
    short_busy_timeout.timeout_s = LOCK_BUSY_TIMEOUT_S

    # Construct the client before taking the lock: schema init also writes.
    client = OutboxClient(db_path=str(db_path), api_url="", auto_flush=False, claim_lease_s=LEASE_S)
    holder = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    holder.isolation_level = None
    holder.execute("BEGIN IMMEDIATE")  # RESERVED lock; blocks another writer

    started = threading.Event()
    errors = []
    outcome = {}

    def claimant():
        started.set()
        try:
            outcome["entry"] = client._claim_next()
        except BaseException as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=claimant, name="busy-claimant")
    try:
        thread.start()
        assert started.wait(timeout=JOIN_TIMEOUT_S)
        thread.join(timeout=JOIN_TIMEOUT_S)
        assert not thread.is_alive(), "busy claimant did not time out"
        assert errors and errors[0].startswith("OperationalError:"), errors
        assert "locked" in errors[0].lower(), errors
        assert "entry" not in outcome, "claim succeeded while a writer held the lock"
        before = _snapshot(db_path)
        assert all(before[oid][0] in ("pending", "failed") for oid in expected)
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    # After the lock is gone the same call takes exactly one row, once.
    entry = client._claim_next()
    assert entry is not None and entry.outbox_id in expected
    after = _snapshot(db_path)
    assert after[entry.outbox_id] == ("claimed", entry.claim_owner, entry.claimed_at)
    second = client._claim_next()
    assert second is not None and second.outbox_id != entry.outbox_id
    assert after[entry.outbox_id] == _snapshot(db_path)[entry.outbox_id]


def test_four_processes_claim_each_row_once(outbox_db):
    """N=4 real OS processes on one DB file: still no double claim."""
    db_path, expected, markers = outbox_db
    # Inherit SSD TMPDIR / PYTHONPYCACHEPREFIX / PYTHONPATH from conftest.py.
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", PROCESS_WORKER, str(db_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    try:
        outputs = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=PROC_TIMEOUT_S)
            assert proc.returncode == 0, stderr
            outputs.append(stdout)
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    claims = []
    for output in outputs:
        claims.extend(tuple(item) for item in json.loads(output))
    _assert_claim_invariants(claims, [], expected, markers, db_path)
