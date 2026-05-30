from assistx.router_integration import _normalize_task_candidate, _priority_for_queue, _queue_matches


def test_normalize_task_candidate_preserves_private_safety_flags() -> None:
    task = _normalize_task_candidate(
        {
            "id": "task-1",
            "title": "Private task",
            "status": "READY",
            "payload_json": '{"queue_class":"batch","privacy":"private","prompt":"Do private work"}',
        }
    )

    assert task["task_id"] == "task-1"
    assert task["priority"] == "batch"
    assert task["local_only"] is True
    assert task["sensitive"] is True
    assert task["allow_cloud"] is False
    assert task["metadata"]["assistx_source"] is True


def test_normalize_task_candidate_maps_background_defaults() -> None:
    task = _normalize_task_candidate(
        {
            "id": "task-2",
            "title": "Docs task",
            "status": "REVIEW",
            "payload_json": '{"description":"Review docs"}',
        }
    )

    assert task["priority"] == "background"
    assert task["sensitive"] is False
    assert task["local_only"] is False
    assert task["allow_cloud"] is True


def test_queue_matching_treats_backlog_as_batch_background() -> None:
    assert _queue_matches({"queue": "batch"}, "backlog") is True
    assert _queue_matches({"queue": "background"}, "backlog") is True
    assert _queue_matches({"queue": "critical"}, "backlog") is False
    assert _queue_matches({"queue": "critical"}, "critical") is True
    assert _queue_matches({"queue": "critical"}, "all") is True


def test_priority_mapping() -> None:
    assert _priority_for_queue("batch", None) == "batch"
    assert _priority_for_queue("background", None) == "background"
    assert _priority_for_queue("critical", None) == "critical"
    assert _priority_for_queue("batch", "normal") == "batch"
    assert _priority_for_queue("batch", "low") == "background"
