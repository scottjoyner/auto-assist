"""Aggregate lossless context-optimization evidence across a corpus."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean, median
from typing import Iterable

from .context_optimization import optimize_context


@dataclass(frozen=True)
class ContextCase:
    case_id: str
    content_type: str
    content: str
    must_preserve: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextCaseResult:
    case_id: str
    content_type: str
    strategy: str
    original_chars: int
    optimized_chars: int
    reduction_ratio: float
    fidelity_passed: bool


@dataclass(frozen=True)
class ContextCorpusReport:
    cases: int
    changed_cases: int
    fidelity_failures: int
    mean_reduction_ratio: float
    median_reduction_ratio: float
    results: tuple[ContextCaseResult, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_context_corpus(cases: Iterable[ContextCase]) -> ContextCorpusReport:
    results: list[ContextCaseResult] = []
    changed_cases = 0
    fidelity_failures = 0
    for case in cases:
        optimized = optimize_context(case.content, case.content_type)
        fidelity = all(value in optimized.content for value in case.must_preserve)
        if case.content_type == "text/plain":
            fidelity = fidelity and optimized.content == case.content
        if optimized.changed:
            changed_cases += 1
        if not fidelity:
            fidelity_failures += 1
        results.append(
            ContextCaseResult(
                case_id=case.case_id,
                content_type=case.content_type,
                strategy=optimized.strategy,
                original_chars=optimized.original_chars,
                optimized_chars=optimized.optimized_chars,
                reduction_ratio=optimized.reduction_ratio,
                fidelity_passed=fidelity,
            )
        )
    reductions = [result.reduction_ratio for result in results]
    return ContextCorpusReport(
        cases=len(results),
        changed_cases=changed_cases,
        fidelity_failures=fidelity_failures,
        mean_reduction_ratio=mean(reductions) if reductions else 0.0,
        median_reduction_ratio=median(reductions) if reductions else 0.0,
        results=tuple(results),
    )
