"""Small, dependency-free behavioral evaluator for stored AssistX traces.

The first PoC deliberately avoids tying the trace contract to Phoenix, TensorZero,
or any other backend.  External observability systems can ingest/export the same
normalized JSON while CI can score fixtures without model calls or network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    score: float
    checks: dict[str, bool]
    reasons: tuple[str, ...]


def _spans(trace: dict[str, Any]) -> list[dict[str, Any]]:
    spans = trace.get("spans", [])
    if not isinstance(spans, list):
        return []
    return [span for span in spans if isinstance(span, dict)]


def _types(spans: Iterable[dict[str, Any]]) -> list[str]:
    return [str(span.get("type", "")) for span in spans]


def evaluate_trace(trace: dict[str, Any]) -> EvaluationResult:
    """Evaluate one normalized trace using deterministic safety/quality checks."""

    spans = _spans(trace)
    span_types = _types(spans)
    terminal = trace.get("outcome", {}) if isinstance(trace.get("outcome"), dict) else {}

    checks = {
        "has_task_span": "task" in span_types,
        "has_route_span": "route" in span_types,
        "has_model_span": "model" in span_types,
        "terminal_success": terminal.get("status") == "success",
        "no_unbounded_retry": sum(1 for t in span_types if t == "retry") <= 2,
        "no_public_route": all(
            span.get("attributes", {}).get("network_path") != "public"
            for span in spans
            if span.get("type") == "route" and isinstance(span.get("attributes"), dict)
        ),
        "evidence_on_success": (
            terminal.get("status") != "success"
            or bool(terminal.get("evidence_ids"))
        ),
    }

    failed = [name for name, passed in checks.items() if not passed]
    score = sum(checks.values()) / len(checks)
    return EvaluationResult(
        passed=not failed,
        score=score,
        checks=checks,
        reasons=tuple(failed),
    )
