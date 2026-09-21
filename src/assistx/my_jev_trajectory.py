from __future__ import annotations

import json
from typing import Any


def _decode_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def enrich_shadow_trajectory(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Derive read-only execution evidence from exported graph records.

    This helper never invents approval or verification facts. An approval is
    emitted only when explicit task approval fields exist, and verification is
    emitted only from recorded acceptance/result evidence.
    """
    tasks = row.get("created_tasks")
    if not isinstance(tasks, list):
        tasks = []
        row["created_tasks"] = tasks

    approvals: list[dict[str, Any]] = []
    task_outcomes: list[dict[str, Any]] = []
    agent_runs: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    verifications: list[dict[str, Any]] = []

    for task in tasks:
        if not isinstance(task, dict):
            continue

        task_id = str(task.get("id") or "")
        approved_by = task.get("approved_by")
        approved_at_ts = task.get("approved_at_ts")
        if approved_by or approved_at_ts is not None:
            approvals.append(
                {
                    "task_id": task_id,
                    "approved_by": approved_by,
                    "approved_at_ts": approved_at_ts,
                }
            )

        task_result = _decode_json(
            task.get("result_json")
        )
        terminal_status = str(
            task.get("status") or ""
        ).upper() in {
            "DONE",
            "FAILED",
            "CANCELLED",
        }
        completed = task.get("completed_at_ts") is not None
        if terminal_status or completed:
            task_outcomes.append(
                {
                    "task_id": task_id,
                    "status": task.get("status"),
                    "completed_by": task.get("completed_by"),
                    "completed_at_ts": task.get("completed_at_ts"),
                    "result_summary": task.get("result_summary"),
                    "result": task_result,
                }
            )

        if isinstance(task_result, dict):
            verification = task_result.get("verification")
            explicit_verified = task_result.get("verified")
            verified_status = (
                task_result.get("status") == "verified"
            )
            if (
                verification is not None
                or isinstance(explicit_verified, bool)
                or verified_status
            ):
                verified = (
                    explicit_verified
                    if isinstance(explicit_verified, bool)
                    else (True if verified_status else None)
                )
                verifications.append(
                    {
                        "task_id": task_id,
                        "source": "task_result",
                        "verified": verified,
                        "evidence": verification,
                    }
                )

        runs = task.get("agent_runs")
        if not isinstance(runs, list):
            continue

        for run in runs:
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("id") or "")
            run_result = _decode_json(
                run.get("result_json")
            )
            agent_runs.append(
                {
                    "task_id": task_id,
                    "run_id": run_id,
                    "agent": run.get("agent"),
                    "model": run.get("model"),
                    "status": run.get("status"),
                    "summary": run.get("summary"),
                    "result": run_result,
                    "started_at_ts": run.get("started_at_ts"),
                    "ended_at_ts": run.get("ended_at_ts"),
                }
            )

            calls = run.get("tool_calls")
            if not isinstance(calls, list):
                continue

            for call in calls:
                if not isinstance(call, dict):
                    continue
                tool = str(call.get("tool") or "")
                tool_input = _decode_json(
                    call.get("input_json")
                )
                tool_output = _decode_json(
                    call.get("output_json")
                )
                call_evidence = {
                    "task_id": task_id,
                    "run_id": run_id,
                    "tool_call_id": str(
                        call.get("id") or ""
                    ),
                    "tool": tool,
                    "ok": call.get("ok"),
                    "input": tool_input,
                    "output": tool_output,
                    "started_at_ts": call.get("started_at_ts"),
                    "ended_at_ts": call.get("ended_at_ts"),
                }
                tool_calls.append(call_evidence)

                if tool == "acceptance":
                    passed = (
                        tool_output.get("passed")
                        if isinstance(tool_output, dict)
                        else None
                    )
                    verifications.append(
                        {
                            "task_id": task_id,
                            "run_id": run_id,
                            "tool_call_id": call_evidence[
                                "tool_call_id"
                            ],
                            "source": "acceptance_tool",
                            "verified": (
                                passed
                                if isinstance(passed, bool)
                                else None
                            ),
                            "evidence": tool_output,
                        }
                    )

    row["trajectory"] = {
        "approvals": approvals,
        "task_outcomes": task_outcomes,
        "agent_runs": agent_runs,
        "tool_calls": tool_calls,
        "verifications": verifications,
    }
    return row
