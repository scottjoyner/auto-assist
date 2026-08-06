from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return sorted({str(item) for item in value if str(item).strip()})
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return _json_list(parsed)
    return []


def benchmark_projection_index(
    neo_factory: Callable[[], Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return role policy plus all benchmark outcomes for signed projection.

    Role policy is projected even when a model has no successful benchmark. A
    quality-floor failure remains explicit so auto-router cannot mistake it for
    an unmeasured unrestricted model.
    """

    neo = neo_factory()
    try:
        with neo._session() as session:
            node_rows = session.run(
                """
                MATCH (n:FleetNode)
                RETURN n.node_id AS node_id,
                       n.roles_json AS roles_json,
                       n.worker_mode AS worker_mode,
                       n.allow_agent_runtime AS allow_agent_runtime,
                       n.allow_code_execution AS allow_code_execution
                """
            )
            node_policy = {
                str(row["node_id"]): {
                    "routing_roles": _json_list(row.get("roles_json")),
                    "worker_mode": str(row.get("worker_mode") or "observer_only"),
                    "allow_agent_runtime": bool(
                        row.get("allow_agent_runtime", False)
                    ),
                    "allow_code_execution": bool(
                        row.get("allow_code_execution", False)
                    ),
                }
                for row in node_rows
                if row.get("node_id")
            }
            rows = session.run(
                """
                MATCH (p:BenchmarkRoutingProfile)
                RETURN properties(p) AS profile
                """
            )
            grouped: dict[tuple[str, str], dict[str, Any]] = {}
            for row in rows:
                profile = dict(row["profile"])
                node_id = str(profile.get("node_id") or "")
                model_id = str(profile.get("model_id") or "")
                if not node_id or not model_id:
                    continue
                policy = node_policy.get(
                    node_id,
                    {
                        "routing_roles": [],
                        "worker_mode": "observer_only",
                        "allow_agent_runtime": False,
                        "allow_code_execution": False,
                    },
                )
                entry = grouped.setdefault(
                    (node_id, model_id),
                    {**policy, "task_family_scores": {}},
                )
                family = str(profile.get("task_family") or "")
                if not family:
                    continue
                entry["task_family_scores"][family] = {
                    "quality_score": profile.get("quality_score"),
                    "quality_confidence": profile.get("quality_confidence"),
                    "reliability": profile.get("reliability"),
                    "tokens_per_second": profile.get("tokens_per_second"),
                    "speed_score": profile.get("speed_score"),
                    "utility_score": profile.get("utility_score"),
                    "quality_floor": profile.get("quality_floor"),
                    "quality_floor_passed": profile.get(
                        "quality_floor_passed"
                    ),
                    "loadout_fingerprint": profile.get(
                        "loadout_fingerprint"
                    ),
                }
            # Models with no profile are attached later by node policy in
            # runtime_projection_v2. This map contains exact model evidence only.
            for entry in grouped.values():
                entry.setdefault("task_family_scores", {})
            return grouped
    finally:
        neo.close()


def node_routing_policy_index(
    neo_factory: Callable[[], Any],
) -> dict[str, dict[str, Any]]:
    neo = neo_factory()
    try:
        with neo._session() as session:
            rows = session.run(
                """
                MATCH (n:FleetNode)
                RETURN n.node_id AS node_id,
                       n.roles_json AS roles_json,
                       n.worker_mode AS worker_mode,
                       n.allow_agent_runtime AS allow_agent_runtime,
                       n.allow_code_execution AS allow_code_execution
                """
            )
            return {
                str(row["node_id"]): {
                    "routing_roles": _json_list(row.get("roles_json")),
                    "worker_mode": str(row.get("worker_mode") or "observer_only"),
                    "allow_agent_runtime": bool(
                        row.get("allow_agent_runtime", False)
                    ),
                    "allow_code_execution": bool(
                        row.get("allow_code_execution", False)
                    ),
                }
                for row in rows
                if row.get("node_id")
            }
    finally:
        neo.close()
