"""Aggregate procedural-memory shadow calibration against eventual outcomes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class ProceduralShadowObservation:
    task_id: str
    rule: str
    score: float
    eligible: bool
    outcome_supported_rule: bool


@dataclass(frozen=True)
class ProceduralCalibrationReport:
    observations: int
    eligible_observations: int
    support_rate: float
    eligible_support_rate: float
    mean_score_supported: float
    mean_score_contradicted: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_procedural_calibration(
    observations: Iterable[ProceduralShadowObservation],
) -> ProceduralCalibrationReport:
    rows = tuple(observations)
    eligible = tuple(row for row in rows if row.eligible)
    supported = tuple(row for row in rows if row.outcome_supported_rule)
    contradicted = tuple(row for row in rows if not row.outcome_supported_rule)
    eligible_supported = tuple(row for row in eligible if row.outcome_supported_rule)
    return ProceduralCalibrationReport(
        observations=len(rows),
        eligible_observations=len(eligible),
        support_rate=(len(supported) / len(rows)) if rows else 0.0,
        eligible_support_rate=(len(eligible_supported) / len(eligible)) if eligible else 0.0,
        mean_score_supported=mean(row.score for row in supported) if supported else 0.0,
        mean_score_contradicted=mean(row.score for row in contradicted) if contradicted else 0.0,
    )
