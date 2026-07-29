from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

PRIORITY_WEIGHT = {"CRITICAL": 1.0, "HIGH": 0.85, "MEDIUM": 0.55, "LOW": 0.25}


def build_allocation_plan(
    tasks: Iterable[dict[str, Any]],
    nodes: Iterable[dict[str, Any]],
    value_matrix: dict[str, Any],
) -> dict[str, Any]:
    """Rank task/node/model placements using value, urgency, fit, and displacement."""
    ready = [task for task in tasks if task.get("status") == "READY"]
    online = [node for node in nodes if node.get("online") or node.get("service_ok")]
    values = value_matrix.get("entries") or []
    recommendations = []
    for task in ready:
        payload = _payload(task)
        required = set(task.get("required_capabilities") or [])
        candidates = []
        for node in online:
            capabilities = set(node.get("capabilities") or [])
            if required and not required.issubset(capabilities | {"llm"}):
                continue
            capacity = max(1, int(node.get("max_concurrent") or node.get("weight") or 1))
            load = min(1.0, float(node.get("inflight_tasks") or 0) / capacity)
            for model in node.get("loaded_models") or [None]:
                value = next(
                    (
                        row for row in values
                        if str(row.get("node_id")) == str(node.get("hostname") or node.get("id"))
                        and (model is None or str(row.get("model_id")) == str(model))
                    ),
                    {},
                )
                quality = float(value.get("quality_score") or 0.5)
                confidence = float(value.get("confidence") or 0.25)
                throughput = min(1.0, float(value.get("tokens_per_second") or 0) / 50.0)
                urgency = PRIORITY_WEIGHT.get(str(task.get("priority") or "MEDIUM").upper(), 0.55)
                queue_class = str(payload.get("queue_class") or "interactive")
                displacement = load * (0.35 if queue_class == "batch" else 0.2)
                score = urgency * 0.28 + quality * 0.27 + throughput * 0.2 + confidence * 0.15 + (1 - load) * 0.1 - displacement
                candidates.append({
                    "node_id": node.get("hostname") or node.get("id"),
                    "model_id": model,
                    "score": round(score, 4),
                    "components": {
                        "urgency": round(urgency, 3),
                        "quality": round(quality, 3),
                        "throughput": round(throughput, 3),
                        "evidence_confidence": round(confidence, 3),
                        "current_load": round(load, 3),
                        "displacement_cost": round(displacement, 3),
                    },
                })
        candidates.sort(key=lambda row: row["score"], reverse=True)
        recommendations.append({
            "task_id": task.get("id"),
            "title": task.get("title"),
            "queue_class": payload.get("queue_class") or "interactive",
            "recommended": candidates[0] if candidates else None,
            "alternatives": candidates[1:4],
            "blocked_reason": None if candidates else "no online capability-compatible placement",
            "executable": False,
        })
    return {
        "recommendations": recommendations,
        "summary": {
            "ready_tasks": len(ready),
            "placeable": sum(1 for row in recommendations if row["recommended"]),
            "blocked": sum(1 for row in recommendations if not row["recommended"]),
        },
        "policy": {
            "objective": "urgency + quality + throughput + evidence - load - displacement",
            "automatic_dispatch": False,
            "preserve_interactive_capacity": True,
        },
    }


def _payload(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("payload") or task.get("payload_json") or {}
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except Exception:
            return {}
    return payload
