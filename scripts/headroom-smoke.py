#!/usr/bin/env python3
"""Run a no-provider-call Headroom compression smoke experiment."""

from __future__ import annotations

import json

from assistx.headroom_adapter import compress_messages_with_headroom


def main() -> int:
    rows = [
        {
            "id": i,
            "status": "ok" if i % 4 else "warning",
            "node": "x1-370",
            "path": f"/var/log/assistx/task-{i}.json",
            "detail": "repeated operational detail that should be structurally compressible",
        }
        for i in range(250)
    ]
    messages = [
        {"role": "system", "content": "Analyze local tool results. Preserve exact identifiers when needed."},
        {"role": "user", "content": "Find warnings in the tool result."},
        {
            "role": "tool",
            "tool_call_id": "call_fixture_1",
            "content": json.dumps({"results": rows}, indent=2),
        },
    ]
    result = compress_messages_with_headroom(messages, model="gpt-4o")
    print(
        json.dumps(
            {
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "tokens_saved": result.tokens_saved,
                "compression_ratio": result.compression_ratio,
                "transforms_applied": list(result.transforms_applied),
            },
            sort_keys=True,
        )
    )
    if result.tokens_before <= 0:
        raise SystemExit("Headroom reported no input tokens")
    if result.tokens_after > result.tokens_before:
        raise SystemExit("Headroom increased token count")
    if result.tokens_saved != result.tokens_before - result.tokens_after:
        raise SystemExit("Headroom token accounting mismatch")
    if not isinstance(result.messages, list) or not result.messages:
        raise SystemExit("Headroom returned no messages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
