"""Initial promotion policies for the starred-repo experiment lanes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .promotion import PromotionThresholds


@dataclass(frozen=True)
class ExperimentPromotionPolicy:
    name: str
    thresholds: PromotionThresholds
    minimum_operational_metrics: dict[str, float]
    required_fault_gates: tuple[str, ...]


POLICIES: dict[str, ExperimentPromotionPolicy] = {
    "context-compression": ExperimentPromotionPolicy(
        name="context-compression",
        thresholds=PromotionThresholds(
            min_runs=10,
            min_pass_rate_delta=0.0,
            min_score_delta=0.0,
            max_mean_duration_ms_delta=0.0,
        ),
        minimum_operational_metrics={
            "input_token_reduction_ratio": 0.15,
            "fidelity_failure_rate": 0.0,
        },
        required_fault_gates=("exact_value_fidelity", "whitespace_sensitive_identity"),
    ),
    "repository-graph": ExperimentPromotionPolicy(
        name="repository-graph",
        thresholds=PromotionThresholds(min_runs=10),
        minimum_operational_metrics={
            "search_or_file_read_reduction_ratio": 0.20,
            "mean_precision": 0.50,
        },
        required_fault_gates=("stale_commit_rejection", "repository_identity_match"),
    ),
    "procedural-memory": ExperimentPromotionPolicy(
        name="procedural-memory",
        thresholds=PromotionThresholds(min_runs=10),
        minimum_operational_metrics={
            "eligible_support_rate": 0.75,
            "repeated_error_reduction_ratio": 0.10,
        },
        required_fault_gates=("single_success_rejection", "conflict_rejection", "invalidation", "supersession"),
    ),
    "cache-affinity": ExperimentPromotionPolicy(
        name="cache-affinity",
        thresholds=PromotionThresholds(
            min_runs=10,
            min_pass_rate_delta=0.0,
            min_score_delta=0.0,
            max_mean_duration_ms_delta=0.0,
        ),
        minimum_operational_metrics={
            "median_ttft_reduction_ratio": 0.10,
            "routing_safety_regressions": 0.0,
        },
        required_fault_gates=("model_hash_invalidation", "quant_invalidation", "context_invalidation", "runtime_invalidation"),
    ),
}


def get_promotion_policy(name: str) -> ExperimentPromotionPolicy:
    try:
        return POLICIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown experiment promotion policy: {name}") from exc


def evaluate_operational_metrics(
    policy: ExperimentPromotionPolicy,
    observed: dict[str, Any],
    *,
    lower_is_better: set[str] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    lower_is_better = lower_is_better or {"fidelity_failure_rate", "routing_safety_regressions"}
    failures: list[str] = []
    for metric, threshold in policy.minimum_operational_metrics.items():
        value = observed.get(metric)
        if not isinstance(value, (int, float)):
            failures.append(f"missing:{metric}")
            continue
        if metric in lower_is_better:
            if float(value) > threshold:
                failures.append(f"threshold:{metric}")
        elif float(value) < threshold:
            failures.append(f"threshold:{metric}")
    return not failures, tuple(failures)
