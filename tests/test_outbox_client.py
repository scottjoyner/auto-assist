import sqlite3
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
    # An unconfigured destination must not consume a retry budget: the event
    # stays pending and no attempt is recorded.
    stats = client.get_stats()
    assert stats["pending"] == 1
    assert stats["failed"] == 0
    assert stats["dead"] == 0
    assert client.skipped_unconfigured == 1
    assert client.get_pending()[0].attempt_count == 0


def test_outbox_exhausted_event_is_terminal_dead_not_failed(tmp_path):
    client = OutboxClient(
        db_path=str(tmp_path / "outbox.sqlite"),
        api_url="http://127.0.0.1:9/never",
        auto_flush=False,
    )
    entry = client.enqueue({"event_id": "event-exhaust"})
    # Pretend the retry budget is almost spent and the backoff window elapsed.
    client._update_status(entry.outbox_id, "pending", 3)
    with client._lock:
        conn = sqlite3.connect(client.db_path)
        conn.execute(
            "UPDATE outbox SET last_attempt_at=? WHERE outbox_id=?",
            ("2026-01-01T00:00:00+00:00", entry.outbox_id),
        )
        conn.commit()
        conn.close()

    assert client.flush(max_attempts=4) == 0

    stats = client.get_stats()
    assert stats["dead"] == 1
    assert stats.get("failed", 0) == 0
    # A terminal row must not be resurrected by a later, more permissive flush.
    assert client.flush(max_attempts=99) == 0
    assert client.get_stats()["dead"] == 1


def test_outbox_enforces_pending_depth_cap(tmp_path, monkeypatch):
    import assistx.outbox_client as oc

    monkeypatch.setattr(oc, "MAX_PENDING", 5)
    client = oc.OutboxClient(db_path=str(tmp_path / "outbox.sqlite"), api_url="", auto_flush=False)
    for i in range(10):
        client.enqueue({"event_id": f"event-cap-{i}"})

    stats = client.get_stats()
    assert stats["pending"] == 5
    assert stats["dead"] == 5


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
