"""Machine-readable promotion gates for experiment candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PromotionThresholds:
    min_runs: int = 5
    min_pass_rate_delta: float = 0.0
    min_score_delta: float = 0.0
    max_mean_duration_ms_delta: float | None = None
    require_zero_safety_regressions: bool = True


@dataclass(frozen=True)
class PromotionDecision:
    eligible: bool
    reasons: tuple[str, ...]
    thresholds: PromotionThresholds
    observed: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "thresholds": asdict(self.thresholds),
            "observed": dict(self.observed),
        }


def evaluate_promotion(
    comparison: dict[str, Any],
    *,
    thresholds: PromotionThresholds,
    safety_regressions: int = 0,
    fault_gates_passed: bool = False,
) -> PromotionDecision:
    baseline = comparison["baseline"]
    candidate = comparison["candidate"]
    reasons: list[str] = []

    if candidate.runs < thresholds.min_runs or baseline.runs < thresholds.min_runs:
        reasons.append("insufficient_runs")
    if float(comparison["pass_rate_delta"]) < thresholds.min_pass_rate_delta:
        reasons.append("pass_rate_regression")
    if float(comparison["score_delta"]) < thresholds.min_score_delta:
        reasons.append("score_regression")
    duration_delta = comparison.get("mean_duration_ms_delta")
    if (
        thresholds.max_mean_duration_ms_delta is not None
        and duration_delta is not None
        and float(duration_delta) > thresholds.max_mean_duration_ms_delta
    ):
        reasons.append("duration_regression")
    if thresholds.require_zero_safety_regressions and safety_regressions != 0:
        reasons.append("safety_regression")
    if not fault_gates_passed:
        reasons.append("fault_gates_not_passed")

    observed = {
        "baseline_runs": baseline.runs,
        "candidate_runs": candidate.runs,
        "pass_rate_delta": comparison["pass_rate_delta"],
        "score_delta": comparison["score_delta"],
        "mean_duration_ms_delta": duration_delta,
        "safety_regressions": safety_regressions,
        "fault_gates_passed": fault_gates_passed,
    }
    return PromotionDecision(not reasons, tuple(reasons), thresholds, observed)
