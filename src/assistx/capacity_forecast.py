from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable


def task_family(task: dict[str, Any]) -> str:
    payload = task.get("payload") or task.get("payload_json") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    value = str(payload.get("task_family") or task.get("kind") or "general").lower()
    for family in ("coding", "reasoning", "extraction", "summarization", "tool_use", "long_context"):
        if family in value:
            return family
    return "general"


def queue_class(task: dict[str, Any]) -> str:
    payload = task.get("payload") or task.get("payload_json") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    value = str(payload.get("queue_class") or task.get("queue_class") or "interactive")
    return value if value in {"critical", "interactive", "batch"} else "interactive"


def build_capacity_forecast(
    tasks: Iterable[dict[str, Any]],
    nodes: Iterable[dict[str, Any]],
    value_matrix: dict[str, Any],
    *,
    target_tokens: int = 2048,
) -> dict[str, Any]:
    task_rows, node_rows = list(tasks), list(nodes)
    ready = [task for task in task_rows if task.get("status") == "READY"]
    running = [task for task in task_rows if task.get("status") in {"CLAIMED", "RUNNING"}]
    ready_by_family = Counter(task_family(task) for task in ready)
    ready_by_class = Counter(queue_class(task) for task in ready)
    online_capacity = sum(
        max(1, int(node.get("max_concurrent") or node.get("weight") or 1))
        for node in node_rows if node.get("online") or node.get("service_ok")
    )
    tps_values = [
        float(row["tokens_per_second"])
        for row in value_matrix.get("entries") or []
        if row.get("loaded") and row.get("online") and row.get("tokens_per_second")
    ]
    aggregate_tps = sum(tps_values)
    tasks_per_hour = aggregate_tps * 3600.0 / max(target_tokens, 1)
    queue_hours = len(ready) / tasks_per_hour if tasks_per_hour > 0 else None
    available_slots = max(0, online_capacity - len(running))

    preemption = []
    urgent_waiting = ready_by_class["critical"] + ready_by_class["interactive"]
    if urgent_waiting > available_slots:
        candidates = sorted(
            (task for task in running if queue_class(task) == "batch"),
            key=lambda task: int(task.get("created_at_ts") or 0),
            reverse=True,
        )
        for task in candidates[: urgent_waiting - available_slots]:
            preemption.append({
                "action": "cooperative_pause_candidate",
                "task_id": task.get("id"),
                "node_id": task.get("claimed_by") or task.get("completed_by"),
                "reason": "interactive or critical demand exceeds immediately available capacity",
                "executable": False,
            })

    bottlenecks = []
    if not online_capacity:
        bottlenecks.append("no online execution capacity")
    if ready and aggregate_tps <= 0:
        bottlenecks.append("no measured TPS for loaded online models")
    if ready_by_class["interactive"] and available_slots == 0:
        bottlenecks.append("interactive queue has no immediately available slot")
    return {
        "summary": {
            "ready": len(ready),
            "running": len(running),
            "online_capacity": online_capacity,
            "available_slots": available_slots,
            "aggregate_loaded_tps": round(aggregate_tps, 2),
            "estimated_tasks_per_hour": round(tasks_per_hour, 2),
            "estimated_queue_hours": round(queue_hours, 3) if queue_hours is not None else None,
        },
        "ready_by_task_family": dict(ready_by_family),
        "ready_by_queue_class": dict(ready_by_class),
        "bottlenecks": bottlenecks,
        "preemption_plan": preemption,
        "admission_policy": {
            "reserve_for_waiting_interactive": True,
            "force_kill": False,
            "batch_admission_allowed": not (
                urgent_waiting > 0 and available_slots <= urgent_waiting
            ),
        },
    }
