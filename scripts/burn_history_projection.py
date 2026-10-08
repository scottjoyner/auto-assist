#!/usr/bin/env python3
"""Bounded read-only trend projection from the append-only AssistX burn receipt ledger.

Each record stores a *rolling 24h gauge*, not a 15-minute token increment.
Never sum gauge snapshots to estimate additional usage or actual vendor debit.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

MAX_TAIL_BYTES = 256 * 1024
MAX_POINTS = 96
WINDOW_SECONDS = 24 * 3600


def _finite_num(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def history_points(path: Path, *, now: float) -> list[dict]:
    """Read a bounded tail; tolerate one incomplete leading line and bad rows."""
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            start = max(0, size - MAX_TAIL_BYTES)
            stream.seek(start)
            raw = stream.read(MAX_TAIL_BYTES)
    except OSError:
        return []
    if start:
        raw = raw.partition(b"\n")[2]
    points = {}
    for line in raw.splitlines()[-MAX_POINTS * 2:]:
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(record, dict) or record.get("schema") != "fleet-provider-burn-audit/v1":
            continue
        stamp = record.get("source_generated_epoch")
        if not _finite_num(stamp) or not now - WINDOW_SECONDS <= stamp <= now + 60:
            continue
        if record.get("complete") is not True or not isinstance(record.get("providers"), list):
            continue
        amounts = {}
        for provider in record["providers"][:64]:
            if not isinstance(provider, dict):
                continue
            key = provider.get("provider")
            value = provider.get("last_24h")
            if isinstance(key, str) and 0 < len(key) <= 140 and _finite_num(value):
                amounts[key] = int(value)
        # Do not infer values for providers absent from a snapshot.
        if amounts:
            points[stamp] = {"epoch": stamp, "providers": amounts, "total": sum(amounts.values())}
    return [points[key] for key in sorted(points)[-MAX_POINTS:]]


def rolling_gauge_trend(points: list[dict], *, provider: str | None = None) -> dict:
    """Change in trailing-24h rollup, NOT incremental tokens burnt in the interval."""
    values = []
    for p in points:
        value = p.get("total") if provider is None else p.get("providers", {}).get(provider)
        if _finite_num(value):
            values.append((p["epoch"], int(value)))
    if not values:
        return {"status": "no_history", "sample_count": 0, "change": None,
                "span_minutes": None, "gauges": []}
    span = values[-1][0] - values[0][0]
    enough = len(values) >= 2 and span >= 5 * 60
    return {"status": "observed" if enough else "insufficient_span",
            "sample_count": len(values), "change": values[-1][1] - values[0][1] if enough else None,
            "span_minutes": round(span / 60, 1), "gauges": [v for _, v in values[-12:]]}
