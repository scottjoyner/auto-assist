from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable
from typing import Any

from .fleet_routing_matrix import current_matrix

_FAMILY_ROLES: dict[str, set[str]] = {
    "coding": {"full_agent", "code_agent"},
    "reasoning": {"full_agent", "reasoning"},
    "tool_use": {"full_agent", "tool_agent"},
    "long_context": {"full_agent", "long_context"},
    "summarization": {"full_agent", "auxiliary_llm", "summarization"},
    "compression": {"full_agent", "auxiliary_llm", "compression"},
    "extraction": {"full_agent", "auxiliary_llm", "extraction"},
}

_ALIASES = {
    "code": "coding",
    "code_review": "coding",
    "research": "reasoning",
    "analysis": "reasoning",
    "tools": "tool_use",
    "tool": "tool_use",
    "summary": "summarization",
    "summarize": "summarization",
    "compress": "compression",
    "extract": "extraction",
    "context": "long_context",
}

_INSTALLED = False


def normalize_family(task: dict[str, Any]) -> str:
    payload = _payload(task)
    value = str(
        payload.get("task_family")
        or payload.get("workload_class")
        or task.get("task_family")
        or task.get("kind")
        or "general"
    ).strip().lower().replace("-", "_").replace(" ", "_")
    return _ALIASES.get(value, value)


def _payload(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("payload") or task.get("payload_json") or {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _strings(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value if str(item).strip()}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [value]
        return _strings(parsed)
    return set()


def _indexes(snapshot: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str, str], dict[str, Any]]]:
    nodes = {
        str(row.get("node_id")): row
        for row in snapshot.get("nodes") or []
        if row.get("node_id")
    }
    profiles = {
        (
            str(row.get("node_id")),
            str(row.get("model_id")),
            str(row.get("task_family")),
        ): row
        for row in snapshot.get("profiles") or []
        if row.get("node_id") and row.get("model_id") and row.get("task_family")
    }
    return nodes, profiles


def _model_candidates(model: Any) -> set[str]:
    if model is None:
        return {""}
    if isinstance(model, dict):
        return {
            str(model.get("id") or ""),
            str(model.get("model_id") or ""),
            str(model.get("model") or ""),
            str(model.get("alias") or ""),
            str(model.get("provider_model") or ""),
        } - {""}
    return {str(model)}


def _profile_for(
    profiles: dict[tuple[str, str, str], dict[str, Any]],
    node_id: str,
    model: Any,
    family: str,
) -> dict[str, Any] | None:
    for model_id in _model_candidates(model):
        profile = profiles.get((node_id, model_id, family))
        if profile is not None:
            return profile
    return None


def _node_allows(node: dict[str, Any], family: str) -> bool:
    worker_mode = str(node.get("worker_mode") or "observer_only")
    if worker_mode in {"observer_only", "benchmark_only"}:
        return False
    roles = _strings(node.get("roles_json") or node.get("roles"))
    required = _FAMILY_ROLES.get(family)
    if required and not roles.intersection(required):
        return False
    if family == "coding" and not bool(node.get("allow_code_execution", False)):
        return False
    return True


def _enrich_nodes(
    raw_nodes: Iterable[dict[str, Any]],
    policies: dict[str, dict[str, Any]],
    family: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in raw_nodes:
        node = copy.deepcopy(raw)
        node_id = str(node.get("hostname") or node.get("id") or node.get("node_id") or "")
        policy = policies.get(node_id)
        if policy is None:
            result.append(node)
            continue
        roles = _strings(policy.get("roles_json") or policy.get("roles"))
        capabilities = set(node.get("capabilities") or []) | roles
        if policy.get("worker_mode") == "auxiliary":
            capabilities.add("llm")
        node["capabilities"] = sorted(capabilities)
        node["worker_mode"] = policy.get("worker_mode")
        node["allow_agent_runtime"] = bool(policy.get("allow_agent_runtime", False))
        node["allow_code_execution"] = bool(policy.get("allow_code_execution", False))
        node["routing_roles"] = sorted(roles)
        if not _node_allows(policy, family):
            node["is_blocked"] = True
            node["control_mode"] = "routing_policy_blocked"
        result.append(node)
    return result


def _family_value_matrix(
    matrix: dict[str, Any],
    profiles: dict[tuple[str, str, str], dict[str, Any]],
    family: str,
) -> dict[str, Any]:
    result = copy.deepcopy(matrix)
    for row in result.get("entries") or []:
        node_id = str(row.get("node_id") or "")
        profile = _profile_for(profiles, node_id, row.get("model_id"), family)
        if profile is None:
            row["benchmark_task_family"] = family
            row["benchmark_profile_present"] = False
            continue
        row["benchmark_task_family"] = family
        row["benchmark_profile_present"] = True
        row["benchmark_quality_floor_passed"] = bool(
            profile.get("quality_floor_passed", False)
        )
        row["quality_score"] = float(profile.get("quality_score") or 0.0)
        row["confidence"] = float(profile.get("quality_confidence") or 0.0)
        row["success_rate"] = float(profile.get("reliability") or 0.0)
        if profile.get("tokens_per_second") is not None:
            row["tokens_per_second"] = float(profile["tokens_per_second"])
        row["routing_utility_score"] = float(profile.get("utility_score") or 0.0)
        if not row["benchmark_quality_floor_passed"]:
            row["quality_score"] = 0.0
            row["success_rate"] = 0.0
    return result


def install_benchmark_allocation_policy(
    api_module: Any,
    neo_factory: Callable[[], Any],
) -> None:
    """Wrap the API's allocation planner with task-family benchmark evidence."""

    global _INSTALLED
    if _INSTALLED:
        return
    original = api_module.build_allocation_plan

    def build_allocation_plan(
        tasks: Iterable[dict[str, Any]],
        nodes: Iterable[dict[str, Any]],
        value_matrix: dict[str, Any],
        skill_profiles: Iterable[dict[str, Any]] = (),
        cache_manifests: Iterable[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        task_rows = list(tasks)
        node_rows = list(nodes)
        try:
            snapshot = current_matrix(neo_factory)
            node_policies, profiles = _indexes(snapshot)
        except Exception:
            node_policies, profiles = {}, {}
        recommendations: list[dict[str, Any]] = []
        for task in task_rows:
            family = normalize_family(task)
            family_nodes = _enrich_nodes(node_rows, node_policies, family)
            family_matrix = _family_value_matrix(value_matrix, profiles, family)
            planned = original(
                [task],
                family_nodes,
                family_matrix,
                skill_profiles=skill_profiles,
                cache_manifests=cache_manifests,
            )
            for recommendation in planned.get("recommendations") or []:
                recommendation["task_family"] = family
                recommendation["routing_matrix_applied"] = bool(node_policies or profiles)
                recommendations.append(recommendation)
        return {
            "recommendations": recommendations,
            "summary": {
                "ready_tasks": len(task_rows),
                "placeable": sum(1 for row in recommendations if row.get("recommended")),
                "blocked": sum(1 for row in recommendations if not row.get("recommended")),
            },
            "policy": {
                "objective": (
                    "operator role eligibility -> task-family quality floor -> "
                    "quality + throughput + evidence + reliability + cache/locality - load"
                ),
                "automatic_dispatch": False,
                "preserve_interactive_capacity": True,
                "routing_matrix_applied": bool(node_policies or profiles),
            },
        }

    api_module.build_allocation_plan = build_allocation_plan
    _INSTALLED = True
