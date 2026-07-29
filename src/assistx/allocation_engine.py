from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from .kv_cache import cache_match, cache_value

PRIORITY_WEIGHT = {"CRITICAL": 1.0, "HIGH": 0.85, "MEDIUM": 0.55, "LOW": 0.25}


def build_allocation_plan(
    tasks: Iterable[dict[str, Any]],
    nodes: Iterable[dict[str, Any]],
    value_matrix: dict[str, Any],
    skill_profiles: Iterable[dict[str, Any]] = (),
    cache_manifests: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Rank task/node/model placements using value, urgency, fit, and displacement."""
    ready = [task for task in tasks if task.get("status") == "READY"]
    online = [node for node in nodes if node.get("online") or node.get("service_ok")]
    values = value_matrix.get("entries") or []
    learned_profiles = list(skill_profiles)
    cache_catalog = list(cache_manifests)
    recommendations = []
    for task in ready:
        payload = _payload(task)
        required = set(task.get("required_capabilities") or [])
        candidates = []
        rejected = []
        for node in nodes:
            node_id = node.get("hostname") or node.get("id")
            if node.get("is_blocked") or node.get("control_mode") in {
                "maintenance",
                "quarantined",
            }:
                rejected.append({"node_id": node_id, "reason": "operator_control"})
            elif not (node.get("online") or node.get("service_ok")):
                rejected.append({"node_id": node_id, "reason": "offline_or_stale"})
        for node in online:
            capabilities = set(node.get("capabilities") or [])
            node_id = node.get("hostname") or node.get("id")
            if node.get("is_blocked") or node.get("control_mode") in {
                "maintenance",
                "quarantined",
            }:
                continue
            if required and not required.issubset(capabilities | {"llm"}):
                rejected.append({
                    "node_id": node_id,
                    "reason": "capability_mismatch",
                    "missing_capabilities": sorted(required - capabilities - {"llm"}),
                })
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
                family = str(task.get("kind") or "task")
                profile = next(
                    (
                        item
                        for item in learned_profiles
                        if str(item.get("model_id")) == str(model)
                        and str(item.get("task_family")) == family
                    ),
                    {},
                )
                attempts = int(profile.get("attempts") or 0)
                verified = int(profile.get("verified_successes") or 0)
                learned_reliability = (
                    (verified + 1) / (attempts + 2)
                    if payload.get("execution_contract")
                    else 0.5
                )
                cache_request = (
                    payload.get("kv_cache")
                    if isinstance(payload.get("kv_cache"), dict)
                    else {}
                )
                cache = cache_match(
                    cache_request,
                    {"node_id": node_id, "model_id": model},
                    cache_catalog,
                )
                cache_economics = cache_value(
                    cache,
                    tokens_per_second=float(
                        value.get("prefill_tokens_per_second")
                        or value.get("tokens_per_second")
                        or 1
                    ),
                    transfer_mib_per_second=float(
                        node.get("cache_transfer_mib_per_second") or 100
                    ),
                )
                affinity_node = str(
                    cache_request.get("affinity_node_id") or ""
                )
                affinity_bonus = (
                    0.04
                    if affinity_node and affinity_node == str(node_id)
                    else 0.0
                )
                score = (
                    urgency * 0.24
                    + quality * 0.22
                    + throughput * 0.16
                    + confidence * 0.12
                    + (1 - load) * 0.1
                    + learned_reliability * 0.16
                    + cache_economics["score_bonus"]
                    + affinity_bonus
                    - displacement
                )
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
                        "verified_code_reliability": round(
                            learned_reliability, 3
                        ),
                        "verified_code_attempts": attempts,
                        "cache_mode": cache_economics["mode"],
                        "cache_id": cache_economics["cache_id"],
                        "cache_prefill_seconds": cache_economics[
                            "prefill_seconds"
                        ],
                        "cache_restore_seconds": cache_economics[
                            "restore_seconds"
                        ],
                        "cache_seconds_saved": cache_economics["seconds_saved"],
                        "cache_locality_bonus": cache_economics["score_bonus"],
                        "session_affinity_bonus": affinity_bonus,
                    },
                })
        candidates.sort(key=lambda row: row["score"], reverse=True)
        recommended = candidates[0] if candidates else None
        opportunity_cost = (
            round(recommended["score"] - candidates[1]["score"], 4)
            if recommended and len(candidates) > 1
            else None
        )
        recommendations.append({
            "task_id": task.get("id"),
            "title": task.get("title"),
            "queue_class": payload.get("queue_class") or "interactive",
            "recommended": recommended,
            "alternatives": candidates[1:4],
            "rejected": rejected,
            "opportunity_cost": opportunity_cost,
            "decision_summary": (
                f"{recommended['node_id']} leads by {opportunity_cost}"
                if opportunity_cost is not None
                else "only one eligible placement"
                if recommended
                else "no eligible placement"
            ),
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
            "objective": (
                "urgency + quality + throughput + evidence + verified task "
                "reliability + cache locality + session affinity "
                "- load - displacement"
            ),
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
