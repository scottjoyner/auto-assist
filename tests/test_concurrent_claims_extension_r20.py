"""Offline extension of the AssistX outbox claim envelope to N=16/32 (r20).

R19 drove the *production* ``OutboxClient._claim_next()`` with N=4/N=8 threads
and N=4 processes against a fresh file SQLite DB. This module pushes the same
real claim path to N=16 and N=32 in-process claimants. The DB is created by
production code at pytest ``tmp_path`` (inside the SSD scratch ``TMPDIR``).
There is no live service, no HTTP delivery, no credential, no live DB write
outside the scratch temp file, and no production-code change.

Harness knobs (nothing touched in production):

* a module-local ``sqlite3`` facade shortens the connect busy timeout so a
  stuck writer fails fast instead of waiting the production 10 s;
* a 1 h claim lease keeps seeded rows from expiring mid-test;
* all claimants start on a barrier and every join is bounded by one shared
  wall-time budget; worker threads are daemons so a budget miss cannot hang
  the process.

Success/busy/error counts and per-call latency are recorded to stdout
(``R20_METRICS <json>``) and, when ``ASSISTX_R20_METRICS`` names a file, as
JSONL under that path (used only for the SSD-scratch measurement artifact).

Safety first: no double claim, no token reuse, no quarantine marker claimed and
unchanged marker rows are asserted *before* any timing-based skip, so a genuine
race is reported rather than hidden. This is one host, one workload, one time
window -- not a universal claimant limit. N=32 is skipped explicitly if the
harness exceeds its budget.
"""

import json
import os
import sqlite3
import threading
import time

import pytest

import assistx.outbox_client as oc
from assistx.outbox_client import OutboxClient

# Verbatim R4 reconciliation hold label, pinned by R18/R19 to the guard.
R4_MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
PAYLOAD = '{"note":"PAYLOAD_SENTINEL_NEVER_PRINTED"}'

N_CLAIMANTS = (16, 32)
CLAIMABLE_PER_CLAIMANT = 4
MIN_CLAIMABLE = 48
BUSY_TIMEOUT_S = 2.0
LOCK_BUSY_TIMEOUT_S = 0.05
TOTAL_BUDGET_S = 15.0
BARRIER_TIMEOUT_S = 10.0
LEASE_S = 3600  # harness-only: no claim can expire during a sub-second test
STALE_CLAIMED_AT = "2026-11-26T00:00:00+00:00"
MARKERS = {"held-pending", "held-stale"}
METRICS_ENV = "ASSISTX_R20_METRICS"


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
def make_r20_db(tmp_path):
    """Factory: a fresh SSD temp DB with claimable rows plus two quarantine holds."""

    def _make(n):
        db_path = tmp_path / f"extension-r20-n{n}.sqlite"
        assert not db_path.exists()
        # Production creates and migrates the schema; no hand-copied DDL.
        OutboxClient(db_path=str(db_path), api_url="", auto_flush=False)
        claimable = max(MIN_CLAIMABLE, n * CLAIMABLE_PER_CLAIMANT)

        rows = []
        for i in range(claimable):
            status = "pending" if i % 2 == 0 else "failed"
            rows.append(
                (
                    f"c-{i:04d}",
                    f"evt-{i:04d}",
                    status,
                    f"2026-11-27T01:{i // 60:02d}:{i % 60:02d}+00:00",
                    0 if status == "pending" else 1,
                    None,
                    None,
                )
            )
        # Both markers are the *oldest* rows: one pending, one stale 'claimed'
        # lease. Without the guard both would be first in line to claim.
        rows.append(("held-pending", "evt-held-pending", "pending", "2026-11-27T00:00:00+00:00", 9, R4_MARKER, None))
        rows.append(
            (
                "held-stale",
                "evt-held-stale",
                "claimed",
                "2026-11-27T00:00:01+00:00",
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
        assert len(expected) == claimable
        assert not expected & MARKERS
        return db_path, expected, claimable

    return _make


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


def _record(summary):
    """Emit a metrics line; optionally append JSONL to the SSD scratch artifact."""
    line = json.dumps(summary, sort_keys=True)
    print("R20_METRICS " + line)
    path = os.environ.get(METRICS_ENV)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _latency(latencies):
    if not latencies:
        return None, None
    return round(1000.0 * sum(latencies) / len(latencies), 3), round(1000.0 * max(latencies), 3)


def _assert_safety(claims, db_path, n):
    """Safety properties that must hold even when the timing budget is missed."""
    ids = [item[0] for item in claims]
    assert len(ids) == len(set(ids)), f"DOUBLE-CLAIM race at N={n}: a row was claimed twice"
    tokens = [item[1] for item in claims]
    assert all(tokens), f"empty owner token at N={n}"
    assert len(tokens) == len(set(tokens)), f"owner-token reuse race at N={n}"
    assert not set(ids) & MARKERS, f"quarantine marker claimed at N={n}"
    assert "LIKE 'quarantine:%'" in oc._QUARANTINE_CLAIM_GUARD

    final = _snapshot(db_path)
    for oid, token, claimed_at, *_ in claims:
        assert final[oid] == ("claimed", token, claimed_at)
    assert final["held-pending"] == ("pending", R4_MARKER, None)
    assert final["held-stale"] == ("claimed", R4_MARKER, STALE_CLAIMED_AT)


def _run_claimants(n, db_path, claimable, *, busy_timeout_s):
    """Release n independent clients on a barrier; collect bounded outcomes."""
    outcomes = [None] * n
    gate = threading.Barrier(n)
    deadline = time.monotonic() + TOTAL_BUDGET_S

    def worker(index):
        client = OutboxClient(db_path=str(db_path), api_url="", auto_flush=False, claim_lease_s=LEASE_S)
        claims, busy, errors, latencies = [], [], [], []
        gate.wait(timeout=BARRIER_TIMEOUT_S)
        for _ in range(claimable + 1):
            if time.monotonic() >= deadline:  # shared total time budget
                break
            started = time.monotonic()
            try:
                entry = client._claim_next()
            except sqlite3.OperationalError as exc:
                latencies.append(time.monotonic() - started)
                message = f"{type(exc).__name__}: {exc}"
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    busy.append(message)
                else:
                    errors.append(message)
                break
            except Exception as exc:
                latencies.append(time.monotonic() - started)
                errors.append(f"{type(exc).__name__}: {exc}")
                break
            latencies.append(time.monotonic() - started)
            if entry is None:
                break
            claims.append((entry.outbox_id, entry.claim_owner, entry.claimed_at, index))
        return claims, busy, errors, latencies

    def run(index):
        try:
            outcomes[index] = worker(index)
        except BaseException as exc:  # surface a harness failure, not a hang
            outcomes[index] = ([], [], [f"worker-{index} {type(exc).__name__}: {exc}"], [])

    started = time.monotonic()
    threads = [threading.Thread(target=run, args=(i,), name=f"r20-claimant-{i}", daemon=True) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    elapsed = time.monotonic() - started
    finished = not any(thread.is_alive() for thread in threads)

    claims = [c for outcome in outcomes if outcome for c in outcome[0]]
    busy = [e for outcome in outcomes if outcome for e in outcome[1]]
    errors = [e for outcome in outcomes if outcome for e in outcome[2]]
    latencies = [v for outcome in outcomes if outcome for v in outcome[3]]
    mean_ms, max_ms = _latency(latencies)
    return {
        "claims": claims,
        "busy": busy,
        "errors": errors,
        "latencies": latencies,
        "elapsed_s": round(elapsed, 3),
        "finished": finished,
        "summary": {
            "success": len(claims),
            "busy": len(busy),
            "errors": len(errors),
            "elapsed_s": round(elapsed, 3),
            "latency_mean_ms": mean_ms,
            "latency_max_ms": max_ms,
            "all_finished": finished,
            "busy_timeout_s": busy_timeout_s,
        },
    }


@pytest.mark.parametrize("n", N_CLAIMANTS)
def test_threaded_claims_beyond_eight(n, make_r20_db, short_busy_timeout):
    """N=16/N=32 real threads: no double claim, guard holds; counts + latency recorded."""
    db_path, expected, claimable = make_r20_db(n)
    result = _run_claimants(n, db_path, claimable, busy_timeout_s=BUSY_TIMEOUT_S)
    claims = result["claims"]

    # Safety before any timing skip: a genuine race is always reported.
    _assert_safety(claims, db_path, n)

    summary = {"case": f"threads-n{n}", "n": n, "claimable": claimable, **result["summary"]}
    _record(summary)

    if n == 32 and (not result["finished"] or result["elapsed_s"] > TOTAL_BUDGET_S):
        pytest.skip(
            f"N=32 harness exceeded the {TOTAL_BUDGET_S:.1f}s budget "
            f"(elapsed {result['elapsed_s']:.2f}s, all_finished={result['finished']}); "
            "no double-claim or guard race observed; "
            "see docs/concurrency-extension-r20.md"
        )

    assert not result["errors"], result["errors"]
    assert claims, "no claim succeeded under contention"
    if not result["busy"]:  # busy is a recorded failure mode, not a fencing race
        assert len(claims) == claimable, f"expected {claimable} claims, got {len(claims)}"
        assert {c[0] for c in claims} == expected


def test_high_contention_busy_surfaces_without_double_claim(make_r20_db, short_busy_timeout):
    """A held writer lock makes 16 claimants fail busy, not double-claim."""
    db_path, expected, claimable = make_r20_db(32)
    n = 16
    # Build every client before taking the lock: construction writes schema.
    clients = [
        OutboxClient(db_path=str(db_path), api_url="", auto_flush=False, claim_lease_s=LEASE_S) for _ in range(n)
    ]
    short_busy_timeout.timeout_s = LOCK_BUSY_TIMEOUT_S

    outcomes = [None] * n
    gate = threading.Barrier(n)
    deadline = time.monotonic() + TOTAL_BUDGET_S

    def worker(index):
        client = clients[index]
        claims, busy, errors, latencies = [], [], [], []
        gate.wait(timeout=BARRIER_TIMEOUT_S)
        if time.monotonic() >= deadline:
            return claims, busy, errors, latencies
        started = time.monotonic()
        try:
            entry = client._claim_next()
        except sqlite3.OperationalError as exc:
            latencies.append(time.monotonic() - started)
            message = f"{type(exc).__name__}: {exc}"
            (busy if "locked" in str(exc).lower() or "busy" in str(exc).lower() else errors).append(message)
        except Exception as exc:
            latencies.append(time.monotonic() - started)
            errors.append(f"{type(exc).__name__}: {exc}")
        else:
            latencies.append(time.monotonic() - started)
            if entry is not None:
                claims.append((entry.outbox_id, entry.claim_owner, entry.claimed_at, index))
        return claims, busy, errors, latencies

    def run(index):
        try:
            outcomes[index] = worker(index)
        except BaseException as exc:
            outcomes[index] = ([], [], [f"worker-{index} {type(exc).__name__}: {exc}"], [])

    holder = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S)
    holder.isolation_level = None
    started = time.monotonic()
    try:
        holder.execute("BEGIN IMMEDIATE")  # RESERVED lock; blocks another writer
        threads = [threading.Thread(target=run, args=(i,), name=f"r20-locked-{i}", daemon=True) for i in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        assert not any(thread.is_alive() for thread in threads), "locked claimant did not finish"
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    elapsed = time.monotonic() - started

    claims = [c for outcome in outcomes if outcome for c in outcome[0]]
    busy = [e for outcome in outcomes if outcome for e in outcome[1]]
    errors = [e for outcome in outcomes if outcome for e in outcome[2]]
    latencies = [v for outcome in outcomes if outcome for v in outcome[3]]
    mean_ms, max_ms = _latency(latencies)

    # Safety first, then the failure-mode observation.
    _assert_safety(claims, db_path, n)
    summary = {
        "case": "held-writer-lock-n16",
        "n": n,
        "claimable": claimable,
        "success": len(claims),
        "busy": len(busy),
        "errors": len(errors),
        "elapsed_s": round(elapsed, 3),
        "latency_mean_ms": mean_ms,
        "latency_max_ms": max_ms,
        "all_finished": True,
        "busy_timeout_s": LOCK_BUSY_TIMEOUT_S,
    }
    _record(summary)
    assert not claims, "a claim succeeded while a writer held the lock"
    assert not errors, errors
    assert len(busy) == n, f"expected {n} busy errors, got {len(busy)}: {busy[:2]}"
    assert all("locked" in message.lower() for message in busy)
    before = _snapshot(db_path)
    assert all(before[oid][0] in ("pending", "failed") for oid in expected)
    assert sum(1 for row in before.values() if row[0] == "claimed") == 1  # stale marker only
