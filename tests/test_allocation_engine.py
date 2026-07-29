from assistx.allocation_engine import build_allocation_plan


def test_allocation_balances_value_and_current_load():
    tasks = [{"id": "task-1", "title": "Urgent diagnosis", "status": "READY", "priority": "HIGH"}]
    nodes = [
        {"hostname": "fast-busy", "online": True, "loaded_models": ["m"], "max_concurrent": 1, "inflight_tasks": 1},
        {"hostname": "steady-free", "online": True, "loaded_models": ["m"], "max_concurrent": 2, "inflight_tasks": 0},
    ]
    values = {"entries": [
        {"node_id": "fast-busy", "model_id": "m", "quality_score": .9, "confidence": .9, "tokens_per_second": 50},
        {"node_id": "steady-free", "model_id": "m", "quality_score": .8, "confidence": .9, "tokens_per_second": 40},
    ]}

    plan = build_allocation_plan(tasks, nodes, values)

    assert plan["summary"] == {"ready_tasks": 1, "placeable": 1, "blocked": 0}
    assert plan["recommendations"][0]["recommended"]["node_id"] == "steady-free"
    assert plan["recommendations"][0]["executable"] is False


def test_allocation_explains_unplaceable_work():
    plan = build_allocation_plan(
        [{"id": "task", "status": "READY", "required_capabilities": ["gpu"]}],
        [{"hostname": "cpu", "online": True, "capabilities": ["cpu"]}],
        {},
    )

    assert plan["summary"]["blocked"] == 1
    assert plan["recommendations"][0]["blocked_reason"]
