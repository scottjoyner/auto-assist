"""Final machine-readable gate between experiment evidence and canary eligibility."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .promotion import PromotionDecision
from .promotion_policies import ExperimentPromotionPolicy, evaluate_operational_metrics


@dataclass(frozen=True)
class CanaryPromotionManifest:
    experiment: str
    manifest_id: str
    statistical_gate_passed: bool
    operational_gate_passed: bool
    required_fault_gates_passed: bool
    operator_approved: bool
    canary_eligible: bool
    blockers: tuple[str, ...]
    observed_operational_metrics: dict[str, Any]
    required_fault_gates: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_canary_promotion_manifest(
    *,
    experiment_manifest_id: str,
    policy: ExperimentPromotionPolicy,
    statistical_decision: PromotionDecision,
    operational_metrics: dict[str, Any],
    passed_fault_gates: set[str],
    operator_approved: bool = False,
) -> CanaryPromotionManifest:
    operational_passed, operational_failures = evaluate_operational_metrics(
        policy, operational_metrics
    )
    missing_faults = tuple(
        gate for gate in policy.required_fault_gates if gate not in passed_fault_gates
    )
    blockers: list[str] = []
    if not statistical_decision.eligible:
        blockers.extend(f"statistical:{reason}" for reason in statistical_decision.reasons)
    blockers.extend(f"operational:{reason}" for reason in operational_failures)
    blockers.extend(f"fault:{gate}" for gate in missing_faults)
    if not operator_approved:
        blockers.append("operator_approval_required")

    return CanaryPromotionManifest(
        experiment=policy.name,
        manifest_id=experiment_manifest_id,
        statistical_gate_passed=statistical_decision.eligible,
        operational_gate_passed=operational_passed,
        required_fault_gates_passed=not missing_faults,
        operator_approved=operator_approved,
        canary_eligible=not blockers,
        blockers=tuple(blockers),
        observed_operational_metrics=dict(operational_metrics),
        required_fault_gates=policy.required_fault_gates,
    )
