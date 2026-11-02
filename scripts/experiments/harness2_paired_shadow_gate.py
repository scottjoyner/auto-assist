#!/usr/bin/env python3
"""Offline Harness²-inspired paired replay evidence gate (no model execution).

Input must be an independently generated JSON summary of paired task results.
No network, subprocess, import of harness2, model loading or task scheduling.
No data from this tool can approve a runtime, claim, harness or graph mutation.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

MAX_FILE_BYTES = 524288
MAX_PAIRS = 500
MIN_SAMPLE = 20
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
TASK = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
EDITABLE_SHADOW = frozenset({"system_prompt", "memory", "tool_guidance", "skills"})


class Rejected(ValueError):
    pass


def _required_string(o: dict, key: str, pattern) -> str:
    value = o.get(key)
    if type(value) is not str or not pattern.fullmatch(value):
        raise Rejected("invalid required identifier")
    return value


def _metric(value: Any, *, integer: bool = False, positive: bool = False, unit: bool = False):
    if integer:
        if type(value) is not int:
            raise Rejected("invalid integer metric")
    elif type(value) not in (int, float):
        raise Rejected("invalid numeric metric")
    if not math.isfinite(float(value)) or value < 0 or (positive and value == 0):
        raise Rejected("nonfinite, negative, or zero metric")
    if unit and value > 1:
        raise Rejected("score outside [0, 1]")
    return value


def evaluate(document: dict) -> dict:
    if type(document) is not dict or document.get("schema_version") != 1:
        raise Rejected("unsupported evidence schema")
    cfg = document.get("configuration")
    if type(cfg) is not dict:
        raise Rejected("missing configuration")
    upstream = _required_string(cfg, "upstream_commit", SHA)
    dataset = _required_string(cfg, "dataset_sha256", DIGEST)
    model = _required_string(cfg, "model_sha256", DIGEST)
    judge = _required_string(cfg, "judge_sha256", DIGEST)
    if cfg.get("mode") not in ("parallel", "sequential"):
        raise Rejected("unsupported recursion mode")
    mode = cfg["mode"]
    k = _metric(cfg.get("k"), integer=True, positive=True)
    if k > 8:
        raise Rejected("unbounded recursion budget")
    seed = _metric(cfg.get("seed"), integer=True)
    evidence_origin = cfg.get("origin")
    if evidence_origin not in ("synthetic", "caller_reported_measured"):
        raise Rejected("unclear evidence origin")
    edits = cfg.get("candidate_components")
    if type(edits) is not list or not edits or len(edits) != len(set(map(str, edits))):
        raise Rejected("candidate edits missing/duplicated")
    if any(type(x) is not str or x not in EDITABLE_SHADOW for x in edits):
        raise Rejected("protected harness component modified")
    rows = document.get("observations")
    if type(rows) is not list or not 2 <= len(rows) <= 2 * MAX_PAIRS or len(rows) % 2:
        raise Rejected("invalid observation count")
    by_task: dict[str, dict[str, dict]] = {}
    for row in rows:
        if type(row) is not dict:
            raise Rejected("observation not object")
        task = _required_string(row, "task_id", TASK)
        source = _required_string(row, "input_sha256", DIGEST)
        variant = row.get("variant")
        if variant not in ("baseline", "candidate") or type(variant) is not str:
            raise Rejected("invalid variant")
        if type(row.get("completed")) is not bool:
            raise Rejected("missing completed flag")
        score = _metric(row.get("judge_score"), unit=True)
        pin = _required_string(row, "judge_sha256", DIGEST)
        if pin != judge:
            raise Rejected("judge identity drift")
        if _required_string(row, "model_sha256", DIGEST) != model:
            raise Rejected("model identity drift")
        p = _metric(row.get("prompt_tokens"), integer=True)
        c = _metric(row.get("completion_tokens"), integer=True)
        ms = _metric(row.get("duration_ms"), integer=True, positive=True)
        # A partial or unscored task is not silently converted into a zero score.
        if row["completed"] is False and score != 0:
            raise Rejected("inconsistent incomplete task score")
        bucket = by_task.setdefault(task, {})
        if variant in bucket:
            raise Rejected("duplicate task/variant")
        bucket[variant] = {"input_sha256": source, "completed": row["completed"],
                           "judge_score": score, "tokens": p + c, "duration_ms": ms}
    for bucket in by_task.values():
        if set(bucket) != {"baseline", "candidate"}:
            raise Rejected("unpaired task")
        if bucket["baseline"]["input_sha256"] != bucket["candidate"]["input_sha256"]:
            raise Rejected("task input drift")
    n = len(by_task)
    a = [bucket["baseline"] for bucket in by_task.values()]
    b = [bucket["candidate"] for bucket in by_task.values()]
    completed_a = sum(int(x["completed"]) for x in a)
    completed_b = sum(int(x["completed"]) for x in b)
    quality_delta = sum(y["judge_score"] - x["judge_score"] for x, y in zip(a, b)) / n
    regressions = sum(y["judge_score"] < x["judge_score"] for x, y in zip(a, b))
    ta, tb = sum(x["tokens"] for x in a), sum(y["tokens"] for y in b)
    la, lb = sum(x["duration_ms"] for x in a), sum(y["duration_ms"] for y in b)
    # These are caller-provided, not cryptographically attested measurements.
    signal = (
        "potential_improvement_for_independent_review"
        if n >= MIN_SAMPLE and quality_delta >= 0.05 and regressions <= n // 10
        and completed_b >= completed_a and tb <= ta * 1.25 and lb <= la * 1.25
        else "insufficient_or_unfavorable_evidence"
    )
    return {
        "kind": "harness2_paired_shadow_evidence_v1",
        "upstream_commit": upstream,
        "dataset_sha256": dataset,
        "model_sha256": model,
        "judge_sha256": judge,
        "mode": mode, "k": k, "seed": seed,
        "evidence_origin": evidence_origin,
        "candidate_components": sorted(edits),
        "paired_tasks": n, "minimum_signal_tasks": MIN_SAMPLE,
        "baseline_completed": completed_a, "candidate_completed": completed_b,
        "candidate_minus_baseline_score_mean": round(quality_delta, 6),
        "score_regression_task_count": regressions,
        "baseline_reported_tokens": ta, "candidate_reported_tokens": tb,
        "baseline_reported_duration_ms": la, "candidate_reported_duration_ms": lb,
        "signal": signal,
        "quality_independently_verified": False,
        "metrics_independently_verified": False,
        "runtime_projection_verified": False,
        "adoption_authorized": False,
        "dispatch_allowed": False,
        "production_gate": "HOLD",
        "reason": "Caller-provided paired experiment evidence is insufficient for identity/claim, quality, or production authorization.",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path)
    args = p.parse_args(argv)
    try:
        path = args.input
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise Rejected("input size")
        d = json.loads(path.read_text("utf-8"),
                       parse_constant=lambda _: (_ for _ in ()).throw(Rejected("nonfinite")))
        report = evaluate(d)
    except (ValueError, OSError, UnicodeError, OverflowError):
        # Never echo untrusted task IDs, inputs, judge content, paths or secrets.
        print("harness2 shadow gate: rejected malformed or untrusted evidence", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
