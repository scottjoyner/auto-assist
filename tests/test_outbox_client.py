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
    assert client.get_stats()["failed"] == 1


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
