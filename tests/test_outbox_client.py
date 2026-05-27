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
