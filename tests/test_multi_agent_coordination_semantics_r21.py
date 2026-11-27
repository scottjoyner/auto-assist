"""Offline contract check for ``docs/multi-agent-coordination-semantics-r21.md``.

Two checks, no production change, no live DB, no HTTP delivery, no credentials:

1. A read-only source assertion that the doc's claim-once, quarantine, lease and
   busy semantics match ``assistx/outbox_client.py`` as it exists on this branch.
2. An in-memory SQLite fixture (shared-cache URI + keeper connection) driving the
   *real* ``OutboxClient._claim_next()`` from four concurrent clients. The only
   added logic is the caller-side bounded retry/backoff the doc recommends for
   ``OperationalError: ... locked``. It proves the documented invariant at N=4:
   every claimable row is claimed exactly once, an operator quarantine hold is
   never claimed (even with an expired lease), and a stale claimed row is
   recovered exactly once with a fresh token.

The in-memory fixture is deliberately in-process: it is an invariant check, not
a new capacity measurement and not a cross-process or production-DB claim.
"""

import json
import random
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import pytest

import assistx.outbox_client as oc

R4_MARKER = "quarantine:stale-attempt9-over-maxattempts5:r4s1-20261126"
PAYLOAD = '{"note":"PAYLOAD_SENTINEL_NEVER_PRINTED"}'
STALE_CLAIMED_AT = "2026-11-20T00:00:00+00:00"
N_CLAIMANTS = 4
EXPECTED_CLAIMABLE = 5  # 4 fresh rows + 1 expired claimed lease
DEADLINE_S = 2.0
BASE_BACKOFF_S = 0.002
MAX_BACKOFF_S = 0.05
MAX_BACKOFF_ATTEMPTS = 6


def _source() -> str:
    """Read-only: the exact production source this branch will ship."""
    return Path(oc.__file__).read_text(encoding="utf-8")


def test_doc_invariants_match_outbox_source():
    """Every behavioural claim in the r21 doc must be visible in the code."""
    src = _source()

    # Quarantine markers are claim_owner labels, not statuses, and the guard is
    # a LIKE pattern (not equality) so every quarantine:* label is covered.
    assert "_QUARANTINE_CLAIM_GUARD = \"(claim_owner IS NULL OR claim_owner NOT LIKE 'quarantine:%')\"" in src

    claim_src = src[src.index("    def _claim_next(") : src.index("    def _release(")]
    # Claim-once fencing: a single write reservation, a SELECT and a guarded
    # UPDATE whose WHERE re-asserts deliverability *and* the quarantine guard.
    assert "BEGIN IMMEDIATE" in claim_src
    assert 'conn.execute("COMMIT")' in claim_src
    assert 'conn.execute("ROLLBACK")' in claim_src
    assert "rowcount" in claim_src
    assert "_QUARANTINE_CLAIM_GUARD" in claim_src
    assert "claim_owner=?, claimed_at=?" in claim_src
    assert "claimed_at < ?" in claim_src  # lease expiry / recovery
    assert "token = uuid.uuid4().hex" in claim_src
    assert "if row is None:" in claim_src  # empty queue / lost race returns None
    assert "if not updated:" in claim_src

    release_src = src[src.index("    def _release(") : src.index("    def _backoff_remaining_s(")]
    # Only the token holder may hand a row back; a recovered row is not clobbered.
    assert "AND status='claimed' AND claim_owner=?" in release_src
    assert '"claim_owner=NULL", "claimed_at=NULL"' in release_src

    # Production lease is 90 s (> the 15 s HTTP delivery timeout), batch is 50.
    assert 'ASSISTX_OUTBOX_CLAIM_LEASE_S", "90"' in src
    assert 'ASSISTX_OUTBOX_FLUSH_BATCH", "50"' in src
    assert "urlopen(req, timeout=15)" in src

    # Busy has no in-production retry: no OperationalError handling anywhere in
    # this module. Delivery backoff exists but is a different path (attempt_count).
    assert "OperationalError" not in src
    assert "retry_base_s * (2 ** min(entry.attempt_count, 16))" in src


class InMemorySqlite:
    """sqlite3 facade: route the production connect() calls to a shared mem DB."""

    Error = sqlite3.Error

    @staticmethod
    def connect(database, *args, **kwargs):
        kwargs["uri"] = True
        return sqlite3.connect(database, *args, **kwargs)


@pytest.fixture
def mem_outbox(monkeypatch):
    """Fresh shared in-memory DB; schema created by production, keeper held open."""
    uri = f"file:coord-r21-{uuid.uuid4().hex}?mode=memory&cache=shared"
    monkeypatch.setattr(oc, "sqlite3", InMemorySqlite)
    keeper = sqlite3.connect(uri, uri=True)
    try:
        oc.OutboxClient(db_path=uri, api_url="", auto_flush=False)  # _init_db/_migrate_schema
        yield uri, keeper
    finally:
        keeper.close()


def _seed(keeper):
    rows = [
        (f"r-{i}", f"e-{i}", "pending" if i % 2 == 0 else "failed", f"2026-11-27T00:00:{i:02d}+00:00", None, None)
        for i in range(4)
    ]
    # Oldest row: operator hold, pending, with an already-expired claimed_at.
    # The quarantine guard must beat lease recovery here.
    rows.append(("held", "e-held", "pending", "2026-11-20T00:00:00+00:00", R4_MARKER, STALE_CLAIMED_AT))
    # Expired claimed lease from a "dead" agent: recoverable exactly once.
    rows.append(("stale", "e-stale", "claimed", "2026-11-20T00:00:01+00:00", "dead-agent-token", STALE_CLAIMED_AT))
    keeper.executemany(
        "INSERT INTO outbox (outbox_id, event_id, payload_json, status, created_at, attempt_count, "
        "claim_owner, claimed_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        [
            (oid, event, PAYLOAD, status, created, owner, claimed)
            for oid, event, status, created, owner, claimed in rows
        ],
    )
    keeper.commit()
    return {r[0] for r in rows if r[4] != R4_MARKER}


def _claim_with_retry(client, deadline, rng, busy_counter):
    """Caller-side bounded retry/backoff for SQLite busy, as recommended in the doc."""
    attempt = 0
    while time.monotonic() < deadline:
        try:
            return client._claim_next()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            busy_counter.append(str(exc))
            attempt += 1
            if attempt > MAX_BACKOFF_ATTEMPTS:
                return None
            delay = min(MAX_BACKOFF_S, BASE_BACKOFF_S * (2 ** min(attempt, 4)))
            time.sleep(delay * (0.5 + rng.random()))
    return None


def test_inmemory_four_claimants_never_double_claim(mem_outbox):
    """N=4 real clients, in-memory DB: no double claim, no marker claim, lease recovered once."""
    uri, keeper = mem_outbox
    expected = _seed(keeper)

    clients = [oc.OutboxClient(db_path=uri, api_url="", auto_flush=False) for _ in range(N_CLAIMANTS)]
    claimed = []
    claimed_lock = threading.Lock()
    busy_errors = []

    def worker(index):
        client = clients[index]
        rng = random.Random(index)
        deadline = time.monotonic() + DEADLINE_S
        local = []
        busy = []
        while time.monotonic() < deadline:
            entry = _claim_with_retry(client, deadline, rng, busy)
            if entry is None:
                break
            local.append((entry.outbox_id, entry.claim_owner, entry.claimed_at))
        with claimed_lock:
            claimed.extend(local)
            busy_errors.extend(busy)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(N_CLAIMANTS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=DEADLINE_S + 5)
        assert not thread.is_alive(), "claimant did not finish within the bounded deadline"

    ids = [row[0] for row in claimed]
    assert len(ids) == EXPECTED_CLAIMABLE, f"expected each claimable row exactly once, got {ids}"
    assert len(set(ids)) == EXPECTED_CLAIMABLE, "a row was claimed more than once"
    assert set(ids) == expected
    assert "held" not in ids, "quarantine marker was claimed"

    tokens = [row[1] for row in claimed]
    assert all(tokens), "a claim without an owner token"
    assert len(set(tokens)) == EXPECTED_CLAIMABLE, "claim owner token reused"

    state = {row[0]: row for row in keeper.execute("SELECT outbox_id, status, claim_owner, claimed_at FROM outbox")}
    for oid in expected:
        assert state[oid][1] == "claimed"
        assert state[oid][2]
        assert state[oid][3] is not None
    assert state["stale"][3] != STALE_CLAIMED_AT, "expired lease was not renewed on recovery"
    assert state["held"] == ("held", "pending", R4_MARKER, STALE_CLAIMED_AT), "quarantine hold was modified"

    print("R21_INMEM " + json.dumps({"claimants": N_CLAIMANTS, "claimed": len(ids), "busy_retried": len(busy_errors)}))
