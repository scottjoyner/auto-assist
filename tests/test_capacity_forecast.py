from assistx.capacity_forecast import build_capacity_forecast


def test_forecast_reserves_capacity_and_recommends_batch_pause():
    tasks = [
        {"id": "urgent", "status": "READY", "payload": {"queue_class": "interactive", "task_family": "coding"}},
        {"id": "batch", "status": "RUNNING", "payload": {"queue_class": "batch"}, "claimed_by": "x1"},
    ]
    nodes = [{"hostname": "x1", "online": True, "max_concurrent": 1}]
    values = {"entries": [{
        "loaded": True, "online": True, "tokens_per_second": 20,
    }]}

    result = build_capacity_forecast(tasks, nodes, values)

    assert result["summary"]["available_slots"] == 0
    assert result["admission_policy"]["batch_admission_allowed"] is False
    assert result["preemption_plan"][0]["task_id"] == "batch"
    assert result["preemption_plan"][0]["executable"] is False


def test_forecast_estimates_queue_completion():
    tasks = [
        {"id": "a", "status": "READY", "kind": "coding"},
        {"id": "b", "status": "READY", "kind": "summarization"},
    ]
    result = build_capacity_forecast(
        tasks,
        [{"online": True, "max_concurrent": 2}],
        {"entries": [{"loaded": True, "online": True, "tokens_per_second": 10}]},
    )

    assert result["summary"]["estimated_tasks_per_hour"] > 0
    assert result["summary"]["estimated_queue_hours"] > 0
    assert result["ready_by_task_family"] == {"coding": 1, "summarization": 1}
