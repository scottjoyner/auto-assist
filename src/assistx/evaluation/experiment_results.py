"""Offline aggregation for AssistX experiment traces."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median
from typing import Any, Iterable

from .trace_eval import evaluate_trace


@dataclass(frozen=True)
class VariantSummary:
    variant: str
    runs: int
    pass_rate: float
    mean_score: float
    mean_duration_ms: float | None
    median_duration_ms: float | None
    p95_duration_ms: float | None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_variant(variant: str, traces: Iterable[dict[str, Any]]) -> VariantSummary:
    rows = list(traces)
    if not rows:
        return VariantSummary(variant, 0, 0.0, 0.0, None, None, None)

    evaluations = [evaluate_trace(trace) for trace in rows]
    durations = [
        float(trace["outcome"]["duration_ms"])
        for trace in rows
        if isinstance(trace.get("outcome"), dict)
        and isinstance(trace["outcome"].get("duration_ms"), (int, float))
    ]
    return VariantSummary(
        variant=variant,
        runs=len(rows),
        pass_rate=sum(result.passed for result in evaluations) / len(evaluations),
        mean_score=mean(result.score for result in evaluations),
        mean_duration_ms=mean(durations) if durations else None,
        median_duration_ms=median(durations) if durations else None,
        p95_duration_ms=_percentile(durations, 0.95),
    )


def compare_variants(
    baseline_name: str,
    baseline_traces: Iterable[dict[str, Any]],
    candidate_name: str,
    candidate_traces: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    baseline = summarize_variant(baseline_name, baseline_traces)
    candidate = summarize_variant(candidate_name, candidate_traces)

    def delta(candidate_value: float | None, baseline_value: float | None) -> float | None:
        if candidate_value is None or baseline_value is None:
            return None
        return candidate_value - baseline_value

    return {
        "baseline": baseline,
        "candidate": candidate,
        "pass_rate_delta": candidate.pass_rate - baseline.pass_rate,
        "score_delta": candidate.mean_score - baseline.mean_score,
        "mean_duration_ms_delta": delta(candidate.mean_duration_ms, baseline.mean_duration_ms),
        "median_duration_ms_delta": delta(candidate.median_duration_ms, baseline.median_duration_ms),
        "p95_duration_ms_delta": delta(candidate.p95_duration_ms, baseline.p95_duration_ms),
    }
