"""Optional Headroom adapter for controlled context-compression experiments.

Headroom remains a lazy dependency. This module does not enable compression in
production request paths; it only normalizes upstream results for A/B benchmarks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class HeadroomCompressionResult:
    messages: list[dict[str, Any]]
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    compression_ratio: float
    transforms_applied: tuple[str, ...]


def compress_messages_with_headroom(
    messages: list[dict[str, Any]],
    *,
    model: str,
    compress_fn: Callable[..., Any] | None = None,
) -> HeadroomCompressionResult:
    """Compress messages using Headroom's public `compress()` API.

    `compress_fn` exists for deterministic tests and lets the core repository
    avoid a mandatory Headroom dependency until the experiment is selected.
    """
    if not model.strip():
        raise ValueError("model is required")
    if compress_fn is None:
        try:
            from headroom import compress as compress_fn  # type: ignore[no-redef]
        except ImportError as exc:
            raise RuntimeError(
                "Headroom is not installed; install `headroom-ai` only in the experiment environment"
            ) from exc

    result = compress_fn(messages, model=model)
    normalized_messages = getattr(result, "messages", None)
    if not isinstance(normalized_messages, list):
        raise ValueError("Headroom result.messages must be a list")

    return HeadroomCompressionResult(
        messages=normalized_messages,
        tokens_before=int(getattr(result, "tokens_before")),
        tokens_after=int(getattr(result, "tokens_after")),
        tokens_saved=int(getattr(result, "tokens_saved")),
        compression_ratio=float(getattr(result, "compression_ratio")),
        transforms_applied=tuple(str(v) for v in getattr(result, "transforms_applied", ())),
    )
