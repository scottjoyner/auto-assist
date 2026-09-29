"""Conservative, dependency-free context reduction baselines.

These helpers are intentionally simple. Upstream compressors such as Headroom
must beat these baselines on token/latency savings without regressing correctness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OptimizationResult:
    content: str
    original_chars: int
    optimized_chars: int
    changed: bool
    strategy: str

    @property
    def reduction_ratio(self) -> float:
        if self.original_chars == 0:
            return 0.0
        return 1.0 - (self.optimized_chars / self.original_chars)


def compact_json_text(content: str) -> OptimizationResult:
    """Minify valid JSON without changing values or key ordering."""
    try:
        parsed: Any = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return OptimizationResult(content, len(content), len(content), False, "identity")

    optimized = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return OptimizationResult(
        optimized,
        len(content),
        len(optimized),
        optimized != content,
        "json_minify",
    )


def optimize_context(content: str, content_type: str) -> OptimizationResult:
    """Apply only lossless baseline transformations for known-safe types."""
    if content_type in {"application/json", "tool/json"}:
        return compact_json_text(content)
    return OptimizationResult(content, len(content), len(content), False, "identity")
