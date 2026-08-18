"""Aggregate repository-graph shadow evidence across coding tasks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Iterable

from .repository_graph_shadow import GraphShadowScore, score_suggestions


@dataclass(frozen=True)
class GraphShadowCase:
    case_id: str
    suggested: tuple[str, ...]
    actual: tuple[str, ...]


@dataclass(frozen=True)
class GraphShadowReport:
    cases: int
    mean_precision: float
    mean_recall: float
    perfect_precision_cases: int
    zero_hit_cases: int
    results: tuple[GraphShadowScore, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_graph_shadow_cases(cases: Iterable[GraphShadowCase]) -> GraphShadowReport:
    results = tuple(score_suggestions(case.suggested, case.actual) for case in cases)
    return GraphShadowReport(
        cases=len(results),
        mean_precision=mean(result.precision for result in results) if results else 0.0,
        mean_recall=mean(result.recall for result in results) if results else 0.0,
        perfect_precision_cases=sum(result.precision == 1.0 for result in results),
        zero_hit_cases=sum(not result.hits for result in results),
        results=results,
    )
