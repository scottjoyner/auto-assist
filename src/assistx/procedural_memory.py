"""Procedural-memory candidate contracts for safe agent self-improvement."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProceduralMemoryCandidate:
    rule: str
    source_run_ids: tuple[str, ...]
    positive_outcomes: int
    negative_outcomes: int
    confidence: float
    scope: str = "coding"

    @property
    def support(self) -> int:
        return self.positive_outcomes + self.negative_outcomes

    @property
    def success_rate(self) -> float:
        if self.support == 0:
            return 0.0
        return self.positive_outcomes / self.support


def validate_candidate(candidate: ProceduralMemoryCandidate) -> None:
    if not candidate.rule.strip():
        raise ValueError("procedural rule must not be empty")
    if not candidate.source_run_ids:
        raise ValueError("procedural rule requires source-run provenance")
    if any(not run_id.strip() for run_id in candidate.source_run_ids):
        raise ValueError("source_run_ids must be non-empty strings")
    if candidate.positive_outcomes < 0 or candidate.negative_outcomes < 0:
        raise ValueError("outcome counts must be non-negative")
    if not 0.0 <= candidate.confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")


def eligible_for_promotion(
    candidate: ProceduralMemoryCandidate,
    *,
    min_support: int = 3,
    min_success_rate: float = 0.75,
    min_confidence: float = 0.70,
) -> bool:
    """Return whether a learned rule is eligible for a later eval-gated promotion."""
    validate_candidate(candidate)
    return (
        candidate.support >= min_support
        and candidate.success_rate >= min_success_rate
        and candidate.confidence >= min_confidence
    )
