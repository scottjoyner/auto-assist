#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from assistx.inference_policy_experiment import (
    compile_trials,
    execute_trial,
    load_cases,
    load_matrix,
    load_shadow_cases,
    summarize_counterfactuals,
)


def _write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            )


def _write_json(path: str | Path, value: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compile or execute the phase-1 AssistX inference-policy replay "
            "experiment. Planning is the default; --execute is required for "
            "network inference."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cases", help="Normalized replay cases JSONL.")
    source.add_argument(
        "--shadow-export",
        help=(
            "JSONL from scripts/export_my_jev_policy_shadow.py. "
            "Rows are converted to unscored replay cases."
        ),
    )
    parser.add_argument("--matrix", required=True, help="Inference-policy matrix JSON.")
    parser.add_argument(
        "--plan-out",
        required=True,
        help="Compiled immutable trial plan JSONL.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually call configured, already-running inference endpoints. "
            "The harness never loads/unloads models."
        ),
    )
    parser.add_argument(
        "--results-out",
        help="Required with --execute; result JSONL.",
    )
    parser.add_argument(
        "--summary-out",
        help="Optional counterfactual summary JSON.",
    )
    parser.add_argument(
        "--baseline-policy-id",
        help="Optional baseline policy for per-case speedup evidence.",
    )
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--include-output",
        action="store_true",
        help=(
            "Store generated text in result rows. Default stores only "
            "hash/length and acceptance evidence."
        ),
    )
    args = parser.parse_args()

    cases = (
        load_cases(args.cases)
        if args.cases
        else load_shadow_cases(args.shadow_export)
    )
    matrix = load_matrix(args.matrix)
    trials = compile_trials(cases, matrix)

    _write_jsonl(args.plan_out, [trial.as_dict() for trial in trials])

    plan_summary = {
        "schema": "assistx-inference-policy-plan-summary-v1",
        "cases": len(cases),
        "policies": len(
            [
                policy
                for policy in matrix["policies"]
                if policy.get("enabled", True)
            ]
        ),
        "trials": len(trials),
        "network_executed": False,
        "allow_model_load": False,
        "routing_authority_changed": False,
        "plan_out": args.plan_out,
    }

    if not args.execute:
        print(json.dumps(plan_summary, indent=2, sort_keys=True))
        return

    if not args.results_out:
        parser.error("--results-out is required with --execute")

    results: list[dict[str, Any]] = []
    for trial in trials:
        try:
            result = execute_trial(
                trial,
                max_tokens=max(1, args.max_tokens),
                timeout_s=max(1.0, args.timeout_seconds),
                include_output=args.include_output,
            )
        except Exception as exc:
            result = {
                "schema": "assistx-inference-policy-result-v1",
                "trial_id": trial.trial_id,
                "case_id": trial.case["case_id"],
                "task_family": trial.case["task_family"],
                "policy_id": trial.policy["policy_id"],
                "node_id": trial.policy["node_id"],
                "model_handle": trial.policy["model_handle"],
                "backend": trial.policy["backend"],
                "quantization": trial.policy["quantization"],
                "speculation": trial.policy["speculation"],
                "context_tokens": trial.policy["context_tokens"],
                "concurrency": trial.policy["concurrency"],
                "execution_mode": "observe_only",
                "allow_model_load": False,
                "routing_authority_changed": False,
                "success": False,
                "error": str(exc)[:600],
                "acceptance_passed": None,
            }
        results.append(result)

    _write_jsonl(args.results_out, results)

    summary = summarize_counterfactuals(
        results,
        baseline_policy_id=args.baseline_policy_id,
    )
    if args.summary_out:
        _write_json(args.summary_out, summary)

    print(
        json.dumps(
            {
                **plan_summary,
                "network_executed": True,
                "results_out": args.results_out,
                "summary_out": args.summary_out,
                "successful_trials": sum(
                    1 for row in results if row.get("success") is True
                ),
                "accepted_trials": sum(
                    1
                    for row in results
                    if row.get("acceptance_passed") is True
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
