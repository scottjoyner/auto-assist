"""Paired output-equivalence harness for context-compression variants.

This module is endpoint-agnostic: callers supply an invocation function. It runs
the same task through raw, lossless JSON, direct Headroom, and safe-hybrid context
and records answer correctness plus context-size/latency evidence.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .context_compression_policy import compress_messages_hybrid
from .context_optimization import compact_json_text
from .headroom_adapter import compress_messages_with_headroom


Messages = list[dict[str, Any]]
InvokeFn = Callable[[Messages, str], str]


@dataclass(frozen=True)
class EquivalenceCase:
    case_id: str
    messages: Messages
    required_output_markers: tuple[str, ...]


@dataclass(frozen=True)
class VariantOutput:
    variant: str
    response: str
    passed: bool
    missing_markers: tuple[str, ...]
    context_chars: int
    invoke_duration_ms: float
    compression_duration_ms: float
    compression_strategy: str


@dataclass(frozen=True)
class EquivalenceCaseResult:
    case_id: str
    variants: tuple[VariantOutput, ...]

    @property
    def raw_passed(self) -> bool:
        return next(v.passed for v in self.variants if v.variant == "raw")

    @property
    def all_candidate_variants_match_correctness(self) -> bool:
        baseline = self.raw_passed
        return all(v.passed == baseline for v in self.variants if v.variant != "raw")


def _context_chars(messages: Messages) -> int:
    return len(json.dumps(messages, sort_keys=True, ensure_ascii=False))


def _lossless_messages(messages: Messages) -> Messages:
    result: Messages = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "tool" and isinstance(item.get("content"), str):
            item["content"] = compact_json_text(item["content"]).content
        result.append(item)
    return result


def _score_response(response: str, markers: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    missing = tuple(marker for marker in markers if marker not in response)
    return not missing, missing


def run_output_equivalence_case(
    case: EquivalenceCase,
    *,
    model: str,
    invoke_fn: InvokeFn,
    headroom_compress_fn: Callable[..., Any] | None = None,
) -> EquivalenceCaseResult:
    """Run one task through all context variants with identical invocation logic."""
    variant_messages: list[tuple[str, Messages, float, str]] = [
        ("raw", [dict(m) for m in case.messages], 0.0, "identity"),
        ("lossless_json", _lossless_messages(case.messages), 0.0, "json_minify"),
    ]

    started = time.perf_counter()
    headroom = compress_messages_with_headroom(
        case.messages,
        model=model,
        compress_fn=headroom_compress_fn,
    )
    headroom_ms = (time.perf_counter() - started) * 1000
    variant_messages.append(
        ("headroom", headroom.messages, headroom_ms, "headroom")
    )

    started = time.perf_counter()
    hybrid = compress_messages_hybrid(
        case.messages,
        model=model,
        compress_fn=headroom_compress_fn,
    )
    hybrid_ms = (time.perf_counter() - started) * 1000
    variant_messages.append(
        ("hybrid", hybrid.messages, hybrid_ms, hybrid.strategy)
    )

    outputs: list[VariantOutput] = []
    for variant, messages, compression_ms, strategy in variant_messages:
        started = time.perf_counter()
        response = str(invoke_fn(messages, variant))
        invoke_ms = (time.perf_counter() - started) * 1000
        passed, missing = _score_response(response, case.required_output_markers)
        outputs.append(
            VariantOutput(
                variant=variant,
                response=response,
                passed=passed,
                missing_markers=missing,
                context_chars=_context_chars(messages),
                invoke_duration_ms=invoke_ms,
                compression_duration_ms=compression_ms,
                compression_strategy=strategy,
            )
        )
    return EquivalenceCaseResult(case_id=case.case_id, variants=tuple(outputs))


def summarize_output_equivalence(
    results: list[EquivalenceCaseResult],
) -> dict[str, Any]:
    variants = ("raw", "lossless_json", "headroom", "hybrid")
    summary: dict[str, Any] = {
        "schema_version": "assistx.context-output-equivalence.v1",
        "cases": len(results),
        "all_candidate_variants_match_raw_correctness": all(
            result.all_candidate_variants_match_correctness for result in results
        ),
        "results": [asdict(result) for result in results],
        "variants": {},
    }
    for variant in variants:
        rows = [next(v for v in result.variants if v.variant == variant) for result in results]
        summary["variants"][variant] = {
            "pass_count": sum(row.passed for row in rows),
            "pass_rate": (sum(row.passed for row in rows) / len(rows)) if rows else 0.0,
            "mean_context_chars": (
                sum(row.context_chars for row in rows) / len(rows) if rows else 0.0
            ),
            "mean_invoke_duration_ms": (
                sum(row.invoke_duration_ms for row in rows) / len(rows) if rows else 0.0
            ),
            "mean_compression_duration_ms": (
                sum(row.compression_duration_ms for row in rows) / len(rows)
                if rows
                else 0.0
            ),
        }
    return summary
