#!/usr/bin/env python3
"""Offline, fail-closed witness for a single exact-free OpenCode execution.

This module never invokes an agent, reads credentials, sends requests, performs
cleanup, or authorizes provider admission. LOCAL_RECEIPT_PASS != billing proof,
output quality acceptance, or independently archived custody.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

LIMIT_BYTES = 16 * 1024 * 1024
SUPPORTED_PROVIDER = "kilo_free"
REQUIRED_TOKENS = ("input", "output", "reasoning")


def canonical_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def receipt_matches(entry: object, raw: bytes) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("sha256"), str)
        and entry["sha256"] == canonical_sha256(raw)
        and nonnegative_int(entry.get("bytes"))
        and entry["bytes"] == len(raw)
    )


def parse_events(raw: bytes) -> tuple[dict, list[str]]:
    usage = {key: 0 for key in REQUIRED_TOKENS}
    usage.update(steps=0, tools=0, text_events=0, error_events=0)
    problems: list[str] = []
    if not raw or len(raw) > LIMIT_BYTES:
        return usage, ["trace_missing_or_oversized"]
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return usage, ["trace_invalid_utf8"]
    for line_number, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            problems.append("trace_blank_event")
            continue
        try:
            event = json.loads(line)
        except ValueError:
            problems.append("trace_invalid_json")
            continue
        if not isinstance(event, dict):
            problems.append("trace_non_object")
            continue
        kind = event.get("type")
        if kind == "step_finish":
            usage["steps"] += 1
            part = event.get("part")
            tokens = part.get("tokens") if isinstance(part, dict) else None
            if not isinstance(tokens, dict):
                problems.append("trace_missing_step_tokens")
                continue
            for key in REQUIRED_TOKENS:
                value = tokens.get(key)
                if not nonnegative_int(value):
                    problems.append("trace_invalid_token_"+key)
                else:
                    usage[key] += value
        elif kind == "tool_use":
            usage["tools"] += 1
        elif kind == "text":
            usage["text_events"] += 1
        elif kind == "error":
            usage["error_events"] += 1
            problems.append("trace_provider_error")
    if not usage["steps"]:
        problems.append("trace_no_finished_step")
    if not usage["text_events"]:
        problems.append("trace_no_text")
    return usage, problems
def assess(
    manifest_raw: bytes,
    stdout_raw: bytes,
    stderr_raw: bytes,
    *,
    expected_model: str,
    max_input: int,
    max_output: int,
    max_steps: int,
    max_tools: int,
) -> dict:
    """Evaluate the final persisted evidence, not an in-flight polling sample."""
    reasons: set[str] = set()
    result = {
        "status": "REJECT",
        "reasons": [],
        "stdout_sha256": canonical_sha256(stdout_raw),
        "stderr_sha256": canonical_sha256(stderr_raw),
        "manifest_sha256": canonical_sha256(manifest_raw),
        "usage": None,
        "model": expected_model,
        "upstream_billing_verified": False,
        "independent_archive_verified": False,
        "output_quality_verified": False,
        "production_authorized": False,
    }
    if (not isinstance(expected_model, str)
            or not expected_model.endswith(":free")
            or expected_model in (":free", "auto:free", "free:free")):
        reasons.add("not_exact_free_model")
    for cap in (max_input, max_output, max_steps, max_tools):
        if not nonnegative_int(cap):
            reasons.add("invalid_limit")
    if not manifest_raw or len(manifest_raw) > LIMIT_BYTES:
        reasons.add("manifest_missing_or_oversized")
        manifest = {}
    else:
        try:
            manifest = json.loads(manifest_raw)
        except (UnicodeError, ValueError):
            manifest = {}
            reasons.add("manifest_invalid_json")
        if not isinstance(manifest, dict):
            manifest = {}
            reasons.add("manifest_non_object")
    if manifest.get("schema") != "subagent-run-manifest/v1":
        reasons.add("manifest_wrong_schema")
    if manifest.get("provider") != SUPPORTED_PROVIDER:
        reasons.add("manifest_wrong_provider")
    if not (isinstance(manifest.get("session_id"), str)
            and manifest["session_id"].strip()):
        reasons.add("manifest_missing_session_id")
    if manifest.get("requested_model") != expected_model or manifest.get("effective_model") != expected_model:
        reasons.add("manifest_model_mismatch")
    if manifest.get("model_identity_complete") is not True:
        reasons.add("manifest_unverified_identity")
    if manifest.get("status") != "completed" or manifest.get("failure_class") is not None:
        reasons.add("manifest_not_completed")
    if manifest.get("last_finish_reason") != "stop":
        reasons.add("manifest_missing_stop")
    cost = manifest.get("cost")
    if type(cost) not in (float, int) or cost != 0:
        reasons.add("reported_cost_nonzero_or_missing")
    if not receipt_matches(manifest.get("stdout"), stdout_raw):
        reasons.add("stdout_receipt_mismatch")
    if not receipt_matches(manifest.get("stderr"), stderr_raw):
        reasons.add("stderr_receipt_mismatch")
    usage, trace_reasons = parse_events(stdout_raw)
    reasons.update(trace_reasons)
    result["usage"] = usage
    tokens = manifest.get("tokens")
    if not isinstance(tokens, dict):
        reasons.add("manifest_missing_tokens")
    else:
        for key in REQUIRED_TOKENS:
            if not nonnegative_int(tokens.get(key)) or tokens[key] != usage[key]:
                reasons.add("manifest_"+key+"_token_mismatch")
    if all(nonnegative_int(cap) for cap in (max_input,max_output,max_steps,max_tools)):
        if usage["input"] > max_input: reasons.add("budget_input")
        if usage["output"] > max_output: reasons.add("budget_output")
        if usage["steps"] > max_steps: reasons.add("budget_steps")
        if usage["tools"] > max_tools: reasons.add("budget_tools")
    result["reasons"] = sorted(reasons)
    result["status"] = "LOCAL_RECEIPT_PASS" if not reasons else "REJECT"
    return result
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--stdout", required=True, type=Path)
    parser.add_argument("--stderr", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-input", type=int, required=True)
    parser.add_argument("--max-output", type=int, required=True)
    parser.add_argument("--max-steps", type=int, required=True)
    parser.add_argument("--max-tools", type=int, required=True)
    args = parser.parse_args()
    try:
        manifest_raw = args.manifest.read_bytes()
        stdout_raw = args.stdout.read_bytes()
        stderr_raw = args.stderr.read_bytes()
    except OSError:
        print(json.dumps({"status":"REJECT","reasons":["evidence_unreadable"]}))
        return 2
    result = assess(
        manifest_raw, stdout_raw, stderr_raw,
        expected_model=args.model,
        max_input=args.max_input, max_output=args.max_output,
        max_steps=args.max_steps, max_tools=args.max_tools,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"]=="LOCAL_RECEIPT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())