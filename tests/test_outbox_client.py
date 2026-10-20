import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from assistx.outbox_client import OutboxClient


def test_outbox_deduplicates_an_event_id(tmp_path):
    client = OutboxClient(db_path=str(tmp_path / "outbox.sqlite"), api_url="", auto_flush=False)

    first = client.enqueue({"event_id": "event-1", "payload": {"private": True}})
    replay = client.enqueue({"event_id": "event-1", "payload": {"private": True}})

    assert replay.outbox_id == first.outbox_id
    assert client.get_stats()["total"] == 1


def test_outbox_without_explicit_destination_does_not_deliver(tmp_path):
    client = OutboxClient(db_path=str(tmp_path / "outbox.sqlite"), api_url="", auto_flush=False)
    client.enqueue({"event_id": "event-2"})

    assert client.flush(max_attempts=1) == 0
    # No destination means no network attempt was made. Never consume the
    # retry budget or mark the event failed for configuration alone.
    assert client.get_stats()["pending"] == 1
    assert client.get_pending()[0].attempt_count == 0


def test_outbox_enforces_pending_depth_cap(tmp_path, monkeypatch):
    import assistx.outbox_client as oc

    monkeypatch.setattr(oc, "MAX_PENDING", 5)
    client = oc.OutboxClient(db_path=str(tmp_path / "outbox.sqlite"), api_url="", auto_flush=False)
    accepted = [
        client.enqueue({"event_id": f"event-cap-{i}"}) for i in range(5)
    ]
    for i in range(5, 10):
        with pytest.raises(oc.OutboxCapacityExceeded):
            client.enqueue({"event_id": f"event-cap-{i}"})

    stats = client.get_stats()
    assert stats["pending"] == 5
    assert stats.get("dead", 0) == 0
    assert stats["total"] == 5
    assert [row.event_id for row in client.get_pending()] == [
        f"event-cap-{i}" for i in range(5)
    ]
    assert client.enqueue({"event_id": "event-cap-0"}).outbox_id == accepted[0].outbox_id
    # Delivering an existing event frees room for another one.
    client._update_status(accepted[0].outbox_id, "delivered", 1)
    client.enqueue({"event_id": "event-cap-new"})
    assert client.get_stats()["pending"] == 5


def test_outbox_purges_terminal_rows_older_than_ttl(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    import assistx.outbox_client as oc

    monkeypatch.setattr(oc, "TTL_DAYS", 7)
    client = oc.OutboxClient(db_path=str(tmp_path / "outbox.sqlite"), api_url="", auto_flush=False)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with client._lock:
        conn = sqlite3.connect(client.db_path)
        try:
            conn.execute(
                "INSERT INTO outbox (outbox_id, event_id, payload_json, attempt_count, last_attempt_at, status, created_at) "
                "VALUES (?, ?, ?, 0, NULL, 'delivered', ?)",
                ("old-1", "evt-old", "{}", old),
            )
            conn.commit()
        finally:
            conn.close()

    client._purge_expired()
    assert client.get_stats()["total"] == 0


def test_two_independent_outbox_writers_cannot_overfill_sqlite(
    tmp_path, monkeypatch
):
    import assistx.outbox_client as oc

    monkeypatch.setattr(oc, "MAX_PENDING", 1)
    database = str(tmp_path / "concurrent-outbox.sqlite")
    first = oc.OutboxClient(db_path=database, api_url="", auto_flush=False)
    second = oc.OutboxClient(db_path=database, api_url="", auto_flush=False)
    gate = Barrier(2)

    def attempt(client, event_id):
        gate.wait(timeout=8)
        try:
            return ("accepted", client.enqueue({"event_id": event_id}).event_id)
        except oc.OutboxCapacityExceeded:
            return ("rejected_at_capacity", event_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(attempt, first, "synthetic-first")
        right = pool.submit(attempt, second, "synthetic-second")
        outcomes = [left.result(timeout=12), right.result(timeout=12)]

    assert sorted(result[0] for result in outcomes) == [
        "accepted", "rejected_at_capacity"
    ]
    assert first.get_stats().get("pending") == 1
    assert first.get_stats().get("dead", 0) == 0
    assert first.get_stats()["total"] == 1


def test_duplicate_is_accepted_at_capacity_without_second_insert(
    tmp_path, monkeypatch
):
    import assistx.outbox_client as oc

    monkeypatch.setattr(oc, "MAX_PENDING", 1)
    client = oc.OutboxClient(
        db_path=str(tmp_path / "dedupe-at-cap.sqlite"),
        api_url="",
        auto_flush=False,
    )
    item = client.enqueue({"event_id": "synthetic-same"})
    replay = client.enqueue({"event_id": "synthetic-same"})
    assert replay.outbox_id == item.outbox_id
    assert client.get_stats()["total"] == 1
    assert client.get_stats()["pending"] == 1
