"""Shadow-mode context optimization telemetry.

Optimization is computed and measured without replacing the context sent to the
live model. This lets real workloads establish savings before behavior changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .context_optimization import optimize_context


@dataclass(frozen=True)
class ContextShadowResult:
    content_type: str
    strategy: str
    original_chars: int
    optimized_chars: int
    reduction_ratio: float
    changed: bool
    preserve_raw_execution: bool = True

    def as_metadata(self) -> dict[str, object]:
        return {"shadow_context_optimization": asdict(self)}


def observe_context_optimization(content: str, content_type: str) -> ContextShadowResult:
    result = optimize_context(content, content_type)
    return ContextShadowResult(
        content_type=content_type,
        strategy=result.strategy,
        original_chars=result.original_chars,
        optimized_chars=result.optimized_chars,
        reduction_ratio=result.reduction_ratio,
        changed=result.changed,
    )
