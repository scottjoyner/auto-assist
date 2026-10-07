#!/usr/bin/env python3
"""Summarize exact free-model run manifests into an auditable scorecard."""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
from datetime import datetime
from typing import Any


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _duration_seconds(record: dict[str, Any]) -> float | None:
    try:
        start = datetime.fromisoformat(str(record["launch_timestamp"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(record["finished_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, (end - start).total_seconds())


def score_run(record: dict[str, Any]) -> int:
    """Transparent 0-100 evidence score; reliability, not model quality."""
    score = 0
    if record.get("status") == "completed":
        score += 45
    if record.get("model_identity_complete") is True:
        score += 15
    cost = _as_float(record.get("cost"))
    if cost == 0.0:
        score += 15
    final_git = record.get("final_git") or {}
    if final_git.get("is_git") and final_git.get("dirty_paths") == 0:
        score += 10
    errors = record.get("raw_error_summary")
    if isinstance(errors, dict) and errors.get("count") == 0:
        score += 5
    if record.get("patch_parts", 0) == 0:
        score += 5
    if record.get("last_finish_reason") == "stop":
        score += 5
    return score


def _manifest_paths(inputs: list[pathlib.Path]) -> list[pathlib.Path]:
    found: set[pathlib.Path] = set()
    for item in inputs:
        if item.is_dir():
            found.update(p for p in item.rglob("*.json") if p.is_file())
        elif item.is_file():
            found.add(item)
    return sorted(found)


def load_manifests(inputs: list[pathlib.Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in _manifest_paths(inputs):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or value.get("schema") != "subagent-run-manifest/v1":
            continue
        record = dict(value)
        record["_manifest_path"] = str(path)
        records.append(record)
    return records
def _sum_tokens(records: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("input", "output", "reasoning", "cache_read", "cache_write")
    return {
        key: sum(int((r.get("tokens") or {}).get(key, 0) or 0) for r in records)
        for key in keys
    }


def _sum_tools(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: collections.Counter[str] = collections.Counter()
    for record in records:
        counts.update(record.get("tools") or {})
    return dict(sorted(counts.items()))


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for record in records:
        model = str(record.get("effective_model") or record.get("requested_model") or "unknown")
        by_model[model].append(record)

    model_rows: list[dict[str, Any]] = []
    for model, group in by_model.items():
        completed = sum(r.get("status") == "completed" for r in group)
        durations = [d for r in group if (d := _duration_seconds(r)) is not None]
        failures = collections.Counter(
            str(r.get("failure_class"))
            for r in group
            if r.get("failure_class")
        )
        raw_errors = sum(int((r.get("raw_error_summary") or {}).get("count", 0) or 0) for r in group)
        scores = [score_run(r) for r in group]
        model_rows.append(
            {
                "model": model,
                "runs": len(group),
                "completed": completed,
                "failed": len(group) - completed,
                "success_rate": completed / len(group),
                "average_evidence_score": sum(scores) / len(scores),
                "average_duration_seconds": (
                    sum(durations) / len(durations) if durations else None
                ),
                "raw_error_events": raw_errors,
                "failure_classes": dict(sorted(failures.items())),
                "tokens": _sum_tokens(group),
                "tools": _sum_tools(group),
                "tool_calls": sum(sum((r.get("tools") or {}).values()) for r in group),
                "total_cost": sum(_as_float(r.get("cost")) or 0.0 for r in group),
            }
        )

    model_rows.sort(
        key=lambda row: (
            -row["average_evidence_score"],
            -row["success_rate"],
            row["average_duration_seconds"] if row["average_duration_seconds"] is not None else float("inf"),
            row["model"],
        )
    )
    completed = sum(r.get("status") == "completed" for r in records)
    return {
        "schema": "free-trace-scorecard/v1",
        "runs": len(records),
        "completed": completed,
        "failed": len(records) - completed,
        "total_cost": sum(_as_float(r.get("cost")) or 0.0 for r in records),
        "tokens": _sum_tokens(records),
        "tools": _sum_tools(records),
        "models": model_rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args(argv)
    result = summarize(load_manifests(args.inputs))
    rendered = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
