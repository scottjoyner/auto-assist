from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .continuity_state import ContinuityConflict, ContinuityStore, now_ms


@dataclass(frozen=True)
class RolePolicy:
    role: str
    required_capabilities: frozenset[str]
    min_memory_available_mb: int = 256
    avoid_capabilities: frozenset[str] = frozenset()
    preferred_capabilities: frozenset[str] = frozenset()


DEFAULT_ROLE_POLICIES = (
    RolePolicy(
        "continuity_leader",
        frozenset({"continuity_state"}),
        min_memory_available_mb=512,
        preferred_capabilities=frozenset({"recovery_island"}),
    ),
    RolePolicy(
        "scheduler_lite",
        frozenset({"continuity_scheduler"}),
        min_memory_available_mb=384,
    ),
    RolePolicy(
        "router_authority",
        frozenset({"router"}),
        min_memory_available_mb=256,
    ),
    RolePolicy(
        "backup_verifier",
        frozenset({"backup_verify"}),
        min_memory_available_mb=512,
    ),
    RolePolicy(
        "durable_committer",
        frozenset({"neo4j_commit"}),
        min_memory_available_mb=3072,
        avoid_capabilities=frozenset({"headless_llm_active"}),
    ),
)


def _service_score(service: Mapping[str, Any], policy: RolePolicy) -> tuple[float, list[str]]:
    reasons: list[str] = []
    status = str(service.get("status") or "offline")
    if status not in {"healthy", "degraded"}:
        return float("-inf"), ["service_not_available"]
    capabilities = {str(item) for item in service.get("capabilities") or []}
    if not policy.required_capabilities.issubset(capabilities):
        return float("-inf"), ["missing_required_capabilities"]
    if policy.avoid_capabilities.intersection(capabilities):
        return float("-inf"), ["conflicting_capability_active"]
    available = int(service.get("memory_available_mb") or 0)
    if available < policy.min_memory_available_mb:
        return float("-inf"), ["insufficient_memory_headroom"]
    max_slots = max(1, int(service.get("max_slots") or 1))
    active_slots = max(0, int(service.get("active_slots") or 0))
    load = min(1.0, active_slots / max_slots)
    score = 100.0
    score += min(40.0, available / 256.0)
    score -= load * 45.0
    if status == "degraded":
        score -= 25.0
        reasons.append("degraded_penalty")
    preferred = policy.preferred_capabilities.intersection(capabilities)
    score += len(preferred) * 15.0
    if preferred:
        reasons.append("preferred_capability")
    reasons.append(f"memory_available_mb={available}")
    reasons.append(f"load={load:.3f}")
    return round(score, 3), reasons


def plan_role_assignments(
    services: Iterable[Mapping[str, Any]],
    *,
    policies: Iterable[RolePolicy] = DEFAULT_ROLE_POLICIES,
) -> list[dict[str, Any]]:
    live = [dict(service) for service in services]
    plans: list[dict[str, Any]] = []
    for policy in policies:
        candidates = []
        for service in live:
            score, reasons = _service_score(service, policy)
            if score == float("-inf"):
                continue
            candidates.append(
                {
                    "role": policy.role,
                    "node_id": service.get("node_id"),
                    "score": score,
                    "reasons": reasons,
                }
            )
        candidates.sort(key=lambda item: (-item["score"], str(item["node_id"])))
        plans.append(
            {
                "role": policy.role,
                "selected": candidates[0] if candidates else None,
                "candidates": candidates,
                "blocked": not candidates,
            }
        )
    return plans


def select_task_node(
    task: Mapping[str, Any],
    services: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    required = {str(item) for item in task.get("required_capabilities") or []}
    preferred_nodes = {str(item) for item in task.get("preferred_nodes") or []}
    candidates = []
    for service in services:
        status = str(service.get("status") or "offline")
        if status not in {"healthy", "degraded"}:
            continue
        caps = {str(item) for item in service.get("capabilities") or []}
        if not required.issubset(caps):
            continue
        max_slots = max(1, int(service.get("max_slots") or 1))
        active_slots = max(0, int(service.get("active_slots") or 0))
        if active_slots >= max_slots:
            continue
        memory = max(0, int(service.get("memory_available_mb") or 0))
        load = active_slots / max_slots
        score = 100.0 - load * 60.0 + min(30.0, memory / 512.0)
        if str(service.get("node_id")) in preferred_nodes:
            score += 30.0
        if status == "degraded":
            score -= 20.0
        candidates.append(
            {
                "node_id": service.get("node_id"),
                "score": round(score, 3),
                "active_slots": active_slots,
                "max_slots": max_slots,
                "memory_available_mb": memory,
            }
        )
    candidates.sort(key=lambda item: (-item["score"], str(item["node_id"])))
    return candidates[0] if candidates else None


class ContinuityCoordinator:
    """Lease roles and distribute bounded tasks without SSH or model lifecycle power."""

    def __init__(self, store: ContinuityStore, *, node_id: str) -> None:
        self.store = store
        self.node_id = node_id

    def role_plan(self) -> list[dict[str, Any]]:
        return plan_role_assignments(self.store.list_services())

    def acquire_selected_roles(self, *, fence_proof: str, ttl_ms: int = 30_000) -> list[dict[str, Any]]:
        epoch = self.store.current_epoch()
        outcomes = []
        for plan in self.role_plan():
            selected = plan.get("selected")
            if not selected or selected.get("node_id") != self.node_id:
                continue
            try:
                lease = self.store.acquire_role_lease(
                    role=plan["role"],
                    holder_node_id=self.node_id,
                    epoch=epoch,
                    ttl_ms=ttl_ms,
                    fence_proof=fence_proof,
                )
                outcomes.append({"role": plan["role"], "ok": True, "lease": lease})
            except ContinuityConflict as exc:
                outcomes.append({"role": plan["role"], "ok": False, "reason": str(exc)})
        return outcomes

    def task_plan(self) -> list[dict[str, Any]]:
        snapshot = self.store.snapshot()
        services = snapshot.get("services") or []
        plans = []
        for task in snapshot.get("tasks") or []:
            if task.get("state") != "queued":
                continue
            plans.append(
                {
                    "task_id": task.get("task_id"),
                    "title": task.get("title"),
                    "selected": select_task_node(task, services),
                }
            )
        return plans

    def status(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "epoch": self.store.current_epoch(),
            "generated_at_ms": now_ms(),
            "role_plan": self.role_plan(),
            "task_plan": self.task_plan(),
        }
