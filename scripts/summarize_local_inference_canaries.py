#!/usr/bin/env python3
"""Read-only metadata usage rollup for bounded local-model canaries, not production billing."""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import pwd
import stat
from datetime import datetime, timedelta, timezone

SCHEMA = "assistx-local-inference-canary/v1"
MAX_FILES = 2000
MAX_BYTES = 16384


def valid_int(value):
    return type(value) is int and value >= 0


def percentile(numbers, quantile):
    if not numbers:
        return None
    values = sorted(numbers)
    if len(values) == 1:
        return round(values[0], 2)
    index = (len(values) - 1) * quantile
    lower = int(index)
    upper = math.ceil(index)
    result = values[lower] + (values[upper] - values[lower]) * (index - lower)
    return round(result, 2)


def inspect_receipt(path, since):
    if path.is_symlink():
        return None, "symlink"
    st = path.stat(follow_symlinks=False)
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
            or (stat.S_IMODE(st.st_mode) & 0o077) or st.st_size > MAX_BYTES):
        return None, "unsafe_file_custody"
    try:
        receipt = json.loads(path.read_text())
    except (ValueError, UnicodeError, OSError):
        return None, "unreadable_json"
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        return None, "unexpected_schema"
    try:
        when = datetime.fromisoformat(receipt["started_utc"])
        if when.tzinfo is None:
            raise ValueError()
    except (TypeError, KeyError, ValueError):
        return None, "invalid_timestamp"
    if when < since:
        return None, "outside_window"
    if not isinstance(receipt.get("request_id"), str) or not isinstance(
            receipt.get("node_claim"), str):
        return None, "missing_identity"
    return receipt, None


def rollup(directory, now=None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    root = pathlib.Path(directory)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("receipt directory unavailable or symlinked")
    files = sorted(root.glob("*.json"))
    if len(files) > MAX_FILES:
        raise ValueError("receipt directory exceeds bounded inventory")
    seen = set()
    events = []
    rejected = {}
    for path in files:
        receipt, reason = inspect_receipt(path, since)
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        key = (receipt["node_claim"], receipt.get("producer"),
               receipt["request_id"])
        if key in seen:
            rejected["duplicate_request"] = rejected.get("duplicate_request", 0) + 1
            continue
        seen.add(key)
        events.append(receipt)

    usage = {"prompt_tokens": 0, "completion_tokens": 0,
             "total_tokens": 0, "cached_prompt_tokens": 0}
    complete = 0
    unknown = 0
    invalid_usage = 0
    quality_pass = 0
    streamed_pass = 0
    latencies = []
    first_contents = []
    for event in events:
        if event.get("result") == "completed" and event.get("matched_expected_ready"):
            quality_pass += 1
            elapsed = event.get("elapsed_ms")
            if isinstance(elapsed, (int, float)) and math.isfinite(elapsed) and elapsed >= 0:
                latencies.append(elapsed)
            first = event.get("first_content_ms")
            if isinstance(first, (int, float)) and math.isfinite(first) and first >= 0:
                first_contents.append(first)
            if event.get("stream_done") is True:
                streamed_pass += 1
        counts = event.get("backend_counts")
        counts = counts if isinstance(counts, dict) else {}
        p, c, t = (counts.get("prompt_tokens"), counts.get("completion_tokens"),
                    counts.get("total_tokens"))
        if not all(valid_int(v) for v in (p, c, t)):
            unknown += 1
            continue
        cache = counts.get("cached_prompt_tokens")
        if p + c != t or (cache is not None and
                          (not valid_int(cache) or cache > p)):
            invalid_usage += 1
            continue
        usage["prompt_tokens"] += p
        usage["completion_tokens"] += c
        usage["total_tokens"] += t
        if cache is not None:
            usage["cached_prompt_tokens"] += cache
        complete += 1
    return {
        "schema": "assistx-local-canary-rollup/v1",
        "generated_utc": now.isoformat(),
        "window_hours": 24,
        "observed_requests": len(events),
        "quality_passing_requests": quality_pass,
        "stream_terminal_passing_requests": streamed_pass,
        "backend_usage_complete": complete,
        "backend_usage_unknown": unknown,
        "backend_usage_inconsistent": invalid_usage,
        "token_counts": usage,
        "e2e_latency_p50_ms": percentile(latencies, .50),
        "e2e_latency_p95_ms": percentile(latencies, .95),
        "stream_first_content_p50_ms": percentile(first_contents, .50),
        "stream_first_content_p95_ms": percentile(first_contents, .95),
        "gateway_confirmed_requests": sum(bool(e.get("gateway_proven")) for e in events),
        "rejected": rejected,
        "note": "Private local canaries only; cache included in prompt, no production coverage/billing proof",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=str(pathlib.Path(
        pwd.getpwuid(os.getuid()).pw_dir) / ".local/state/assistx-local-canary"))
    args = parser.parse_args()
    try:
        result = rollup(args.directory)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0 if not result["backend_usage_inconsistent"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
