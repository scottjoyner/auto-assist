#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from assistx.inference_policy_experiment import load_matrix
from assistx.inference_soak_campaign import (
    compile_campaign_plan,
    evaluate_campaign,
    load_campaign_config,
    load_summaries,
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


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def _render_commands(plan: dict[str, Any], output_dir: str) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated AssistX soak campaign command sheet.",
        "# It does NOT start/restart inference runtimes.",
        "# Before each command, bind the matching runtime/telemetry env vars",
        "# to a freshly started dedicated process with frozen revision/launch SHA.",
        "",
        f"OUT={json.dumps(output_dir)}",
        'mkdir -p "$OUT"',
        "",
    ]
    for candidate in plan.get("candidates") or []:
        lines.extend(
            [
                f"# === {candidate['candidate_id']} ===",
                (
                    "# "
                    + json.dumps(
                        candidate["signature"],
                        sort_keys=True,
                    )
                ),
            ]
        )
        for run in candidate.get("runs") or []:
            mode = run["mode"]
            lines.extend(
                [
                    "",
                    (
                        "# FRESH RUNTIME REQUIRED before "
                        + mode
                        + " for policy "
                        + candidate["source_policy_id"]
                    ),
                    (
                        "PYTHONPATH=src python "
                        "scripts/run_assistx_inference_session_soak.py "
                        f"--profiles {json.dumps(plan['profiles_file'])} "
                        f"--profile-id {json.dumps(run['profile_id'])} "
                        f"--matrix {json.dumps(plan['matrix_file'])} "
                        f"--policy-id {json.dumps(run['policy_id'])} "
                        f"--mode {json.dumps(mode)} "
                        f"--turns {int(run['turns'])} "
                        f"--plan-out \"$OUT/{run['plan_file']}\" "
                        "--execute "
                        f"--results-out \"$OUT/{run['results_file']}\" "
                        f"--summary-out \"$OUT/{run['summary_file']}\" "
                        f"--checkpoint-dir \"$OUT/{run['checkpoint_dir']}\""
                    ),
                ]
            )
        lines.append("")
    lines.extend(
        [
            "# After every 32K pair is complete, evaluate advancement:",
            "# python scripts/manage_assistx_soak_campaign.py evaluate ...",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or evaluate the AssistX 32K-to-128K soak campaign. "
            "Evaluation can authorize only the next experiment context, "
            "never production routing."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan")
    plan_parser.add_argument("--config", required=True)
    plan_parser.add_argument("--matrix", required=True)
    plan_parser.add_argument("--profiles", required=True)
    plan_parser.add_argument("--output", required=True)
    plan_parser.add_argument("--commands-out")
    plan_parser.add_argument(
        "--evidence-dir",
        default="/tmp/assistx-soak-campaign",
    )

    eval_parser = sub.add_parser("evaluate")
    eval_parser.add_argument("--plan", required=True)
    eval_parser.add_argument(
        "--summaries",
        nargs="+",
        required=True,
    )
    eval_parser.add_argument("--output", required=True)

    args = parser.parse_args()

    if args.command == "plan":
        config = load_campaign_config(args.config)
        matrix = load_matrix(args.matrix)
        plan = compile_campaign_plan(
            config,
            matrix,
            profiles_file=args.profiles,
            matrix_file=args.matrix,
        )
        _write_json(args.output, plan)
        if args.commands_out:
            commands_path = Path(args.commands_out)
            commands_path.parent.mkdir(parents=True, exist_ok=True)
            commands_path.write_text(
                _render_commands(
                    plan,
                    args.evidence_dir,
                ),
                encoding="utf-8",
            )
        print(
            json.dumps(
                {
                    "campaign_id": plan["campaign_id"],
                    "plan_sha256": plan["plan_sha256"],
                    "candidates": len(plan["candidates"]),
                    "runs": sum(
                        len(candidate["runs"])
                        for candidate in plan["candidates"]
                    ),
                    "output": args.output,
                    "commands_out": args.commands_out,
                    "network_executed": False,
                    "production_promotion_authorized": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    plan = _load_json(args.plan)
    summaries = load_summaries(args.summaries)
    evidence = evaluate_campaign(plan, summaries)
    _write_json(args.output, evidence)
    print(
        json.dumps(
            {
                "campaign_id": evidence["campaign_id"],
                "evaluated_candidates": len(
                    evidence["evaluated_candidates"]
                ),
                "eligible_target_context_policies": len(
                    evidence["eligible_target_context_policies"]
                ),
                "output": args.output,
                "production_promotion_authorized": False,
                "routing_authority_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
