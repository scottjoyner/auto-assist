#!/usr/bin/env python3
"""Capture a measured (circumstance, loadout) outcome into the policy file.

The recommender refuses to let the decision model score a circumstance until
every feasible candidate has a measured outcome. This script is how those
outcomes get captured: probe the live node, record what was observed, and
append the record with ``provenance: measured``.

It never changes what is resident. Load, unload, and bind actions belong to
the operator (see ``recommend_fleet_loadout.py --commands-out``); this script
only measures.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assistx.fleet_loadout import Policy  # noqa: E402


def _vram() -> tuple[int, int]:
    total = used = 0
    for card in sorted(Path("/sys/class/drm").glob("card*")):
        for name, sink in (("mem_info_vram_total", "t"), ("mem_info_vram_used", "u")):
            path = card / "device" / name
            if not path.exists():
                continue
            try:
                value = int(path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            if sink == "t":
                total += value
            else:
                used += value
    return total, used


def _completion(
    endpoint: str, model: str, samples: int, max_tokens: int, timeout: float
) -> dict[str, Any]:
    latencies: list[float | None] = []
    error: str | None = None
    last_text: str | None = None
    for _ in range(samples):
        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: LLM PATH OK"}],
                "max_tokens": max_tokens,
                "temperature": 0,
            }
        ).encode()
        request = urllib.request.Request(
            f"{endpoint.rstrip('/')}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        started = time.time()
        try:
            payload = json.loads(urllib.request.urlopen(request, timeout=timeout).read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            latencies.append(None)
            continue
        latencies.append(round((time.time() - started) * 1000.0, 1))
        last_text = payload["choices"][0]["message"].get("content")
    good = sorted(value for value in latencies if value is not None)
    return {
        "samples_ms": latencies,
        "median_ms": good[len(good) // 2] if good else None,
        "error": error,
        "last_content": (last_text or "")[:120] or None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record a measured loadout outcome into the policy evidence set."
    )
    parser.add_argument("--policy", required=True)
    parser.add_argument("--circumstance-id", required=True)
    parser.add_argument("--loadout-id", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:1234")
    parser.add_argument("--model", default="refinedtoolcallv5-3b")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--note", default="")
    parser.add_argument("--extra", help="JSON object merged into outcomes")
    parser.add_argument("--output")
    args = parser.parse_args()

    policy = Policy.load(args.policy)
    known = {loadout["loadout_id"] for loadout in policy.loadouts}
    if args.loadout_id not in known:
        raise SystemExit(f"unknown loadout_id {args.loadout_id!r}; known: {sorted(known)}")

    completion = _completion(
        args.endpoint, args.model, args.samples, args.max_tokens, args.timeout
    )
    total, used = _vram()
    outcomes: dict[str, Any] = {
        "endpoint": args.endpoint,
        "completion_healthy": completion["error"] is None
        and any(value is not None for value in completion["samples_ms"]),
        "completion_median_ms": completion["median_ms"],
        "completion_samples_ms": completion["samples_ms"],
        "completion_error": completion["error"],
        "completion_last_content": completion["last_content"],
        "vram_total_bytes": total,
        "vram_used_bytes": used,
    }
    if args.extra:
        try:
            extra = json.loads(args.extra)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--extra is not valid JSON: {exc}") from exc
        if not isinstance(extra, dict):
            raise SystemExit("--extra must be a JSON object")
        outcomes.update(extra)

    record = {
        "circumstance_id": args.circumstance_id,
        "loadout_id": args.loadout_id,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provenance": "measured",
        "outcomes": outcomes,
        "notes": args.note,
    }
    policy.raw.setdefault("evidence", [])
    policy.raw["evidence"] = [
        item
        for item in policy.raw["evidence"]
        if not (
            item.get("circumstance_id") == args.circumstance_id
            and item.get("loadout_id") == args.loadout_id
        )
    ]
    policy.raw["evidence"].append(record)
    text = json.dumps(policy.raw, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        Path(args.policy).write_text(text, encoding="utf-8")
    print(json.dumps(record, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
