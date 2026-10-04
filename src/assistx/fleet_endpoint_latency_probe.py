"""Explicit, observation-only OpenAI-compatible stream latency probes.

This module never discovers endpoints and never mutates routing/admission state.
Callers must supply an already-known endpoint and model identity explicitly.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from typing import Any


FALSE_AUTHORITY = {
    "routing_authority_changed": False,
    "admission_changed": False,
    "dispatch_allowed": False,
    "approval_granted": False,
    "claim_acquired": False,
    "mutation_allowed": False,
}


def _text_present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return value is not None


def probe_openai_stream(
    *,
    base_url: str,
    model_id: str,
    prompt: str = "Reply with exactly OK",
    max_tokens: int = 16,
    timeout_seconds: float = 30.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Measure one bounded stream without granting the endpoint any authority.

    TTFT is the first generated semantic token, defined as reasoning_content
    (or a compatible reasoning field) OR ordinary content, whichever arrives
    first. First SSE and first ordinary content are retained separately.
    """

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max(1, int(max_tokens)),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    started = clock()
    first_sse_ms: float | None = None
    first_reasoning_ms: float | None = None
    first_content_ms: float | None = None
    first_token_ms: float | None = None
    usage: dict[str, Any] | None = None
    reasoning_events = 0
    content_events = 0

    with opener(request, timeout=timeout_seconds) as response:
        headers_ms = (clock() - started) * 1000.0
        while True:
            raw = response.readline()
            if not raw:
                break
            if isinstance(raw, str):
                line = raw.encode()
            else:
                line = raw
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            if data == b"[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            event_ms = (clock() - started) * 1000.0
            if first_sse_ms is None:
                first_sse_ms = event_ms

            event_usage = event.get("usage")
            if isinstance(event_usage, dict):
                usage = event_usage

            for choice in event.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    continue
                reasoning = (
                    delta.get("reasoning_content")
                    if "reasoning_content" in delta
                    else delta.get("reasoning")
                )
                content = delta.get("content")

                if _text_present(reasoning):
                    reasoning_events += 1
                    if first_reasoning_ms is None:
                        first_reasoning_ms = event_ms
                    if first_token_ms is None:
                        first_token_ms = event_ms

                if _text_present(content):
                    content_events += 1
                    if first_content_ms is None:
                        first_content_ms = event_ms
                    if first_token_ms is None:
                        first_token_ms = event_ms

    total_ms = (clock() - started) * 1000.0
    if first_sse_ms is None:
        raise ValueError("stream produced no parseable SSE event")
    if first_token_ms is None:
        raise ValueError("stream produced no reasoning or content token")

    return {
        "schema": "assistx-fleet-endpoint-stream-sample-v1",
        "base_url": base_url.rstrip("/"),
        "model_id": model_id,
        "ttft_basis": "reasoning_or_content",
        "http_headers_ms": round(headers_ms, 3),
        "first_sse_event_ms": round(first_sse_ms, 3),
        "ttft_ms": round(first_token_ms, 3),
        "first_reasoning_ms": (
            round(first_reasoning_ms, 3) if first_reasoning_ms is not None else None
        ),
        "first_content_ms": (
            round(first_content_ms, 3) if first_content_ms is not None else None
        ),
        "wall_ms": round(total_ms, 3),
        "reasoning_events": reasoning_events,
        "content_events": content_events,
        "usage": usage,
        "evidence_only": True,
        "authority": dict(FALSE_AUTHORITY),
    }
