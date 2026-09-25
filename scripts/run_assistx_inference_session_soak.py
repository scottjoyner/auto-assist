#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from assistx.inference_policy_experiment import load_matrix
from assistx.inference_session_soak import (
    build_turn_case,
    check_session_runtime_identity,
    compile_soak_plan,
    execute_soak_turn,
    finalize_pending_commit,
    initial_state,
    load_soak_profile,
    load_state,
    messages_for_turn,
    save_state,
    stage_pending_commit,
    summarize_soak_results,
    validate_resume_state,
    verify_pending_result_row,
)


def _write_json(path: str | Path, value: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    )
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        os.fsync(handle.fileno())
    temp.replace(output)


def _append_jsonl(path: str | Path, value: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            + "\n"
        )
        handle.flush()


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return output
    with p.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"{path}:{line_number}: expected JSON object"
                )
            output.append(value)
    return output


def _policy_by_id(
    matrix: dict[str, Any],
    policy_id: str,
) -> dict[str, Any]:
    for policy in matrix.get("policies") or []:
        if str(policy.get("policy_id") or "") == policy_id:
            if not policy.get("enabled", True):
                raise ValueError(
                    f"policy {policy_id} is disabled in the matrix"
                )
            return policy
    raise ValueError(f"unknown policy_id: {policy_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compile or run a resumable long-session AssistX inference soak. "
            "Planning is the default; --execute is required for network use."
        )
    )
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--matrix", required=True)
    parser.add_argument("--policy-id", required=True)
    parser.add_argument(
        "--mode",
        choices=["stable_prefix", "growing_prefix"],
        required=True,
    )
    parser.add_argument(
        "--turns",
        type=int,
        help="Override the profile default, bounded by profile min/max.",
    )
    parser.add_argument("--plan-out", required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run the soak against already-running inference/telemetry endpoints.",
    )
    parser.add_argument(
        "--results-out",
        help="Required with --execute; append-only per-turn JSONL.",
    )
    parser.add_argument(
        "--summary-out",
        help="Required with --execute; current session summary JSON.",
    )
    parser.add_argument(
        "--checkpoint-dir",
        help="Required with --execute; durable local resume state.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the exact matching checkpoint and result stream.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace local soak result/checkpoint files for a fresh run.",
    )
    parser.add_argument("--max-tokens", type=int, default=192)
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument(
        "--telemetry-timeout-seconds",
        type=float,
        default=5.0,
    )
    args = parser.parse_args()

    profile = load_soak_profile(args.profiles, args.profile_id)
    matrix = load_matrix(args.matrix)
    policy = _policy_by_id(matrix, args.policy_id)
    plan = compile_soak_plan(
        profile,
        policy,
        mode=args.mode,
        turns=args.turns,
    )
    _write_json(args.plan_out, plan)

    if not args.execute:
        print(
            json.dumps(
                {
                    "plan_out": args.plan_out,
                    "network_executed": False,
                    **plan,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if not args.results_out:
        parser.error("--results-out is required with --execute")
    if not args.summary_out:
        parser.error("--summary-out is required with --execute")
    if not args.checkpoint_dir:
        parser.error("--checkpoint-dir is required with --execute")
    if args.resume and args.overwrite:
        parser.error("--resume and --overwrite are mutually exclusive")

    checkpoint_dir = Path(args.checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    state_path = checkpoint_dir / f"{plan['session_id']}.state.json"
    results_path = Path(args.results_out)
    summary_path = Path(args.summary_out)

    if args.resume:
        if not state_path.exists():
            parser.error("--resume requested but checkpoint does not exist")
        state = validate_resume_state(
            load_state(state_path),
            plan,
        )
        rows = _read_jsonl(results_path)
        completed = int(state["completed_turns"])
        pending = state.get("pending_commit")
        if isinstance(pending, dict):
            if len(rows) == completed:
                pending_result = pending.get("result")
                if not isinstance(pending_result, dict):
                    raise ValueError(
                        "checkpoint pending result is missing or invalid"
                    )
                _append_jsonl(results_path, pending_result)
                rows.append(pending_result)
            elif len(rows) == completed + 1:
                verify_pending_result_row(
                    state,
                    rows[-1],
                )
            else:
                raise ValueError(
                    "result stream cannot be reconciled with pending checkpoint"
                )
            finalize_pending_commit(state)
            save_state(state_path, state)

        if len(rows) != int(state["completed_turns"]):
            raise ValueError(
                "result row count does not match checkpoint completed_turns"
            )
        for row in rows:
            if row.get("session_id") != plan["session_id"]:
                raise ValueError(
                    "result stream contains a different soak session"
                )
    else:
        existing = [
            path
            for path in (state_path, results_path, summary_path)
            if path.exists()
        ]
        if existing and not args.overwrite:
            raise ValueError(
                "fresh soak would overwrite existing files; use --resume "
                "or explicit --overwrite"
            )
        if args.overwrite:
            for path in (state_path, results_path, summary_path):
                if path.exists():
                    path.unlink()
        state = initial_state(plan)
        rows = []
        save_state(state_path, state)

    stop_reason: str | None = None
    start_turn = int(state["completed_turns"]) + 1
    total_turns = int(plan["turns"])

    for turn_index in range(start_turn, total_turns + 1):
        messages = messages_for_turn(profile, state)
        case = build_turn_case(
            profile,
            session_id=plan["session_id"],
            turn_index=turn_index,
            total_turns=total_turns,
            messages=messages,
        )
        result, output_text = execute_soak_turn(
            profile,
            policy,
            session_id=plan["session_id"],
            turn_index=turn_index,
            total_turns=total_turns,
            messages=messages,
            max_tokens=max(1, args.max_tokens),
            timeout_s=max(1.0, args.timeout_seconds),
            telemetry_timeout_s=max(
                1.0,
                args.telemetry_timeout_seconds,
            ),
        )
        result["mode"] = plan["mode"]
        result["target_context_tokens"] = plan[
            "target_context_tokens"
        ]

        identity_ok, identity_error = check_session_runtime_identity(
            state,
            result,
        )
        result["session_runtime_identity_valid"] = identity_ok
        if not identity_ok:
            result["success"] = False
            existing = str(result.get("error") or "").strip()
            result["error"] = (
                existing
                + ("; " if existing else "")
                + str(identity_error or "session runtime identity changed")
            )[:600]
            result["telemetry_valid"] = False

        stage_pending_commit(
            state,
            result=result,
            user_message=case["messages"][-1],
            assistant_output=output_text,
        )
        save_state(state_path, state)

        _append_jsonl(results_path, result)
        rows.append(result)

        finalize_pending_commit(state)
        save_state(state_path, state)

        summary = summarize_soak_results(
            profile,
            plan,
            rows,
        )
        _write_json(summary_path, summary)

        # Once telemetry attribution or session identity is broken, later
        # observations cannot be safely assigned to this frozen session.
        if result.get("telemetry_valid") is not True:
            stop_reason = (
                str(result.get("error") or "")
                or "telemetry attribution became invalid"
            )
            break

    summary = summarize_soak_results(
        profile,
        plan,
        rows,
    )
    summary["stopped_early"] = len(rows) < total_turns
    summary["stop_reason"] = stop_reason
    _write_json(summary_path, summary)

    print(
        json.dumps(
            {
                "session_id": plan["session_id"],
                "profile_id": plan["profile_id"],
                "policy_id": plan["policy_id"],
                "mode": plan["mode"],
                "completed_turns": len(rows),
                "expected_turns": total_turns,
                "passed": summary["passed"],
                "stopped_early": summary["stopped_early"],
                "stop_reason": stop_reason,
                "results_out": str(results_path),
                "summary_out": str(summary_path),
                "checkpoint": str(state_path),
                "routing_authority_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
