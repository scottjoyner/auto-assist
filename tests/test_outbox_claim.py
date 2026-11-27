"""Claim protocol: no concurrent double delivery, stale claims recoverable."""

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from assistx.outbox_client import OutboxClient


def _client(tmp_path, **kwargs):
    return OutboxClient(
        db_path=str(tmp_path / "outbox.sqlite"),
        api_url="http://assistx.invalid",
        auto_flush=False,
        **kwargs,
    )


def _age_claim(db_path, outbox_id, seconds):
    """Backdate a claim so it looks abandoned by a crashed flusher."""
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE outbox SET status='claimed', claim_owner='ghost', claimed_at=? "
            "WHERE outbox_id=?",
            (stamp, outbox_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row(db_path, outbox_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT status, attempt_count, claim_owner, claimed_at FROM outbox "
            "WHERE outbox_id=?",
            (outbox_id,),
        ).fetchone()
    finally:
        conn.close()


def test_concurrent_flush_does_not_double_deliver(tmp_path, monkeypatch):
    """Two flushers on one DB must deliver each row exactly once."""
    first = _client(tmp_path)
    total = 12
    for i in range(total):
        first.enqueue({"event_id": f"evt-race-{i}"})

    deliveries = []
    lock = threading.Lock()

    def fake_deliver(self, entry):
        # Long enough that an overlapping flusher would have picked the same
        # row up under the old select-then-deliver window.
        time.sleep(0.01)
        with lock:
            deliveries.append(entry.outbox_id)
        return True

    monkeypatch.setattr(OutboxClient, "_deliver", fake_deliver)

    # Distinct clients => the old code's in-process lock did not protect them.
    other = _client(tmp_path)
    results = {}

    def run(name, client):
        results[name] = client.flush()

    threads = [
        threading.Thread(target=run, args=("a", first)),
        threading.Thread(target=run, args=("b", other)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(deliveries) == total, f"expected {total} deliveries, got {len(deliveries)}"
    assert len(set(deliveries)) == total, "a row was delivered by both flushers"
    assert results["a"] + results["b"] == total
    stats = first.get_stats()
    assert stats["delivered"] == total
    assert stats["pending"] == 0
    assert stats["claimed"] == 0


def test_stale_claim_is_recovered(tmp_path, monkeypatch):
    client = _client(tmp_path)
    entry = client.enqueue({"event_id": "evt-stale"})
    _age_claim(client.db_path, entry.outbox_id, client.claim_lease_s * 4)

    # A crashed claimer leaves status='claimed'; the lease expiry must make the
    # row selectable again rather than stranding it forever.
    claimed = client._claim_next()
    assert claimed is not None
    assert claimed.outbox_id == entry.outbox_id
    assert claimed.claim_owner != "ghost"

    client._release(claimed, "pending", 0, touch=False)

    seen = []
    monkeypatch.setattr(
        OutboxClient,
        "_deliver",
        lambda self, e: (seen.append(e.outbox_id), True)[1],
    )
    assert client.flush() == 1
    assert seen == [entry.outbox_id]
    assert _row(client.db_path, entry.outbox_id)[0] == "delivered"


def test_fresh_claim_is_not_stolen(tmp_path, monkeypatch):
    owner = _client(tmp_path)
    entry = owner.enqueue({"event_id": "evt-fresh"})
    claimed = owner._claim_next()
    assert claimed is not None and claimed.status == "claimed"

    # Same DB, different flusher, lease still valid.
    rival = _client(tmp_path)
    assert rival._claim_next() is None

    seen = []
    monkeypatch.setattr(
        OutboxClient,
        "_deliver",
        lambda self, e: (seen.append(e.outbox_id), True)[1],
    )
    assert rival.flush() == 0
    assert seen == []
    status, attempt_count, claim_owner, _ = _row(owner.db_path, entry.outbox_id)
    assert status == "claimed"
    assert claim_owner == claimed.claim_owner
    assert attempt_count == 0

    # The owner still completes the delivery it was holding.
    assert owner._release(claimed, "delivered", 1) is True
    assert _row(owner.db_path, entry.outbox_id)[0] == "delivered"


def test_losing_claimer_cannot_clobber_a_recovered_row(tmp_path):
    client = _client(tmp_path, claim_lease_s=1)
    entry = client.enqueue({"event_id": "evt-lost-lease"})
    first = client._claim_next()
    assert first is not None

    _age_claim(client.db_path, entry.outbox_id, 60)  # lease expired in between
    second = client._claim_next()
    assert second is not None and second.claim_owner != first.claim_owner

    # The flusher that lost the lease must not write the row.
    assert client._release(first, "delivered", 1) is False
    assert _row(client.db_path, entry.outbox_id)[0] == "claimed"


def test_claim_migration_is_additive_on_legacy_database(tmp_path):
    """A pre-claim DB keeps its rows and gains the columns in place."""
    db_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE outbox (
                outbox_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                attempt_count INTEGER DEFAULT 0,
                last_attempt_at TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO outbox (outbox_id, event_id, payload_json, attempt_count, "
            "last_attempt_at, status, created_at) VALUES "
            "('legacy-1', 'evt-legacy', '{}', 9, '2026-11-14T14:41:19+00:00', "
            "'pending', '2026-10-06T02:59:16+00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    client = OutboxClient(db_path=str(db_path), api_url="", auto_flush=False)

    columns = {
        row[1]
        for row in sqlite3.connect(db_path).execute("PRAGMA table_info(outbox)").fetchall()
    }
    assert {"claim_owner", "claimed_at"} <= columns
    assert client.get_stats()["pending"] == 1
    health = client.queue_health()
    assert health["pending"] == 1
    # 2026-10-06 is far past any threshold: the wedged-backlog shape.
    assert health["oldest_pending_age_s"] > 86400 * 30
    assert health["ok"] is False


def test_flush_batch_is_bounded(tmp_path, monkeypatch):
    client = _client(tmp_path, flush_batch=3)
    for i in range(7):
        client.enqueue({"event_id": f"evt-batch-{i}"})

    monkeypatch.setattr(OutboxClient, "_deliver", lambda self, e: True)
    assert client.flush() == 3
    assert client.get_stats()["pending"] == 4
    assert client.flush() == 3
    assert client.flush() == 1
    assert client.get_stats()["pending"] == 0
