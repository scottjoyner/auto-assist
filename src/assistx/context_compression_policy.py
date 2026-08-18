"""Safety-first admission policy for optional Headroom compression experiments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .context_optimization import compact_json_text
from .headroom_adapter import HeadroomCompressionResult, compress_messages_with_headroom


_ESCAPE_SENSITIVE_MARKERS = ("\\n", "\\r", "\\t", "\\\"")


@dataclass(frozen=True)
class HybridCompressionResult:
    messages: list[dict[str, Any]]
    strategy: str
    bypassed_headroom: bool
    bypass_reason: str | None
    headroom: HeadroomCompressionResult | None


def _tool_json_escape_sensitive(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            json.loads(content)
        except json.JSONDecodeError:
            continue
        if any(marker in content for marker in _ESCAPE_SENSITIVE_MARKERS):
            return True
    return False


def _tool_json_structured(messages: list[dict[str, Any]]) -> bool:
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            value = json.loads(content)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return True
    return False


def _lossless_tool_json_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    optimized: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "tool" and isinstance(item.get("content"), str):
            item["content"] = compact_json_text(item["content"]).content
        optimized.append(item)
    return optimized


def compress_messages_hybrid(
    messages: list[dict[str, Any]],
    *,
    model: str,
    compress_fn: Callable[..., Any] | None = None,
) -> HybridCompressionResult:
    """Use Headroom only when the payload clears conservative fidelity fencing.

    Escape-sensitive structured tool results are routed to the lossless stdlib
    baseline instead of being sent through lossy/content-aware compression.
    """
    if _tool_json_escape_sensitive(messages):
        reason = "escape_sensitive_tool_json"
    elif _tool_json_structured(messages):
        reason = "structured_tool_json"
    else:
        reason = None

    if reason is not None:
        return HybridCompressionResult(
            messages=_lossless_tool_json_messages(messages),
            strategy="lossless_json_fallback",
            bypassed_headroom=True,
            bypass_reason=reason,
            headroom=None,
        )

    headroom = compress_messages_with_headroom(
        messages,
        model=model,
        compress_fn=compress_fn,
    )
    return HybridCompressionResult(
        messages=headroom.messages,
        strategy="headroom",
        bypassed_headroom=False,
        bypass_reason=None,
        headroom=headroom,
    )
