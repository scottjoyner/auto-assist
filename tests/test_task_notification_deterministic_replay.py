"""Synthetic canonical task re-notification has one stable outbox identity.

All tests use SQLite in a disposable temporary directory with auto_flush=False.
No task graph, receiver endpoint, credentials or network requests are accessed.
"""
from uuid import NAMESPACE_URL, uuid5

from assistx import auto_assign_client as notifier
from assistx import outbox_client as oc


def _test_outbox(tmp_path, monkeypatch, *, capacity=1):
    monkeypatch.setattr(oc, "MAX_PENDING", capacity)
    queue = oc.OutboxClient(
        db_path=str(tmp_path / "synthetic-notifications.sqlite"),
        api_url="",
        auto_flush=False,
    )
    monkeypatch.setattr(notifier, "_outbox", queue)
    monkeypatch.setattr(
        notifier.settings, "auto_assign_base_url", "https://example.invalid"
    )
    return queue


def test_repeated_task_notification_is_deduplicated_by_event_identity(
    tmp_path, monkeypatch
):
    queue = _test_outbox(tmp_path, monkeypatch)
    for _ in range(7):
        assert notifier.notify_task_created(task_id="synthetic-task-a")
    rows = queue.get_pending()
    assert len(rows) == 1
    assert rows[0].attempt_count == 0
    assert queue.get_stats()["pending"] == 1
    key = "assistx-task-created:synthetic-task-a"
    assert rows[0].event_id == f"evt_{uuid5(NAMESPACE_URL, key).hex}"


def test_retry_other_task_cannot_evict_existing_notification(
    tmp_path, monkeypatch
):
    queue = _test_outbox(tmp_path, monkeypatch, capacity=1)
    assert notifier.notify_task_created("synthetic-task-a") is True
    assert notifier.notify_task_created("synthetic-task-b") is False
    assert notifier.notify_task_created("synthetic-task-a") is True
    assert len(queue.get_pending()) == 1
    assert queue.get_pending()[0].attempt_count == 0


def test_task_correlation_is_stable_unless_explicitly_given(
    monkeypatch, tmp_path
):
    queue = _test_outbox(tmp_path, monkeypatch)
    assert notifier.notify_task_created("synthetic-task-a")
    import json

    original = json.loads(queue.get_pending()[0].payload_json)
    assert original["correlation_id"] == uuid5(
        NAMESPACE_URL, "assistx-task-created:synthetic-task-a:trace"
    ).hex

    # First accepted event is authoritative in the durable outbox. Replays
    # cannot overwrite its original trace links or payload by accident.
    assert notifier.notify_task_created(
        "synthetic-task-a", correlation_id="synthetic-new-trace"
    )
    stored = json.loads(queue.get_pending()[0].payload_json)
    assert stored == original


def test_two_different_task_ids_generate_separate_event_identities(
    monkeypatch, tmp_path
):
    queue = _test_outbox(tmp_path, monkeypatch, capacity=3)
    assert notifier.notify_task_created("synthetic-a")
    assert notifier.notify_task_created("synthetic-b")
    rows = queue.get_pending()
    assert len({row.event_id for row in rows}) == 2
    assert all(row.event_id.startswith("evt_") for row in rows)
