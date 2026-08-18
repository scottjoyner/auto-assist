"""Held-out evaluation for procedural-memory candidates.

This module scores whether shadow-retrieved rules correlate with better later
coding outcomes. It never injects a rule into an active agent context.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable

from .procedural_memory import ProceduralMemoryCandidate
from .procedural_memory_shadow import shadow_match


@dataclass(frozen=True)
class HeldOutTaskOutcome:
    task_id: str
    task_text: str
    success: bool
    repeated_error: bool
    searches: int = 0
    verification_retries: int = 0
    time_to_first_correct_plan_ms: float | None = None
    supporting_rules: tuple[str, ...] = ()
    contradicted_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class HeldOutObservation:
    task_id: str
    retrieved_rules: tuple[str, ...]
    eligible_rules: tuple[str, ...]
    supported_eligible_rules: tuple[str, ...]
    contradicted_eligible_rules: tuple[str, ...]
    success: bool
    repeated_error: bool
    searches: int
    verification_retries: int
    time_to_first_correct_plan_ms: float | None

    @property
    def eligible_support_rate(self) -> float | None:
        denominator = len(self.eligible_rules)
        if denominator == 0:
            return None
        return len(self.supported_eligible_rules) / denominator


def evaluate_heldout_task(
    outcome: HeldOutTaskOutcome,
    candidates: Iterable[ProceduralMemoryCandidate],
    *,
    limit: int = 5,
) -> HeldOutObservation:
    matches = shadow_match(outcome.task_text, candidates, limit=limit)
    retrieved = tuple(match.rule for match in matches)
    eligible = tuple(match.rule for match in matches if match.eligible)
    support = set(outcome.supporting_rules)
    contradicted = set(outcome.contradicted_rules)
    return HeldOutObservation(
        task_id=outcome.task_id,
        retrieved_rules=retrieved,
        eligible_rules=eligible,
        supported_eligible_rules=tuple(rule for rule in eligible if rule in support),
        contradicted_eligible_rules=tuple(rule for rule in eligible if rule in contradicted),
        success=outcome.success,
        repeated_error=outcome.repeated_error,
        searches=max(0, int(outcome.searches)),
        verification_retries=max(0, int(outcome.verification_retries)),
        time_to_first_correct_plan_ms=outcome.time_to_first_correct_plan_ms,
    )


def summarize_heldout(
    observations: list[HeldOutObservation],
    *,
    baseline_repeated_error_rate: float | None = None,
) -> dict[str, object]:
    eligible_observations = [o for o in observations if o.eligible_rules]
    supported = sum(len(o.supported_eligible_rules) for o in eligible_observations)
    eligible = sum(len(o.eligible_rules) for o in eligible_observations)
    contradicted = sum(len(o.contradicted_eligible_rules) for o in eligible_observations)
    success_rate = (
        sum(o.success for o in observations) / len(observations) if observations else 0.0
    )
    repeated_error_rate = (
        sum(o.repeated_error for o in observations) / len(observations)
        if observations
        else 0.0
    )
    repeated_error_reduction_ratio = None
    if baseline_repeated_error_rate is not None and baseline_repeated_error_rate > 0:
        repeated_error_reduction_ratio = (
            baseline_repeated_error_rate - repeated_error_rate
        ) / baseline_repeated_error_rate

    plan_times = [
        o.time_to_first_correct_plan_ms
        for o in observations
        if o.time_to_first_correct_plan_ms is not None
    ]
    return {
        "schema_version": "assistx.procedural-memory-heldout.v1",
        "observations": len(observations),
        "eligible_observations": len(eligible_observations),
        "eligible_rule_retrievals": eligible,
        "supported_eligible_rule_retrievals": supported,
        "contradicted_eligible_rule_retrievals": contradicted,
        "eligible_support_rate": (supported / eligible) if eligible else 0.0,
        "contradiction_rate": (contradicted / eligible) if eligible else 0.0,
        "task_success_rate": success_rate,
        "repeated_error_rate": repeated_error_rate,
        "baseline_repeated_error_rate": baseline_repeated_error_rate,
        "repeated_error_reduction_ratio": repeated_error_reduction_ratio,
        "mean_searches": mean([o.searches for o in observations]) if observations else 0.0,
        "mean_verification_retries": (
            mean([o.verification_retries for o in observations]) if observations else 0.0
        ),
        "mean_time_to_first_correct_plan_ms": mean(plan_times) if plan_times else None,
        "authoritative_behavior_changed": False,
    }
