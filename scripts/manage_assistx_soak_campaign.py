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
    evaluate_target_context,
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


def _render_advance_commands(
    plan: dict[str, Any],
    evidence: dict[str, Any],
    output_dir: str,
) -> str:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated AssistX target-context command sheet.",
        "# Contains only candidates that cleared the source-context gate.",
        "# It does NOT start/restart inference runtimes.",
        "",
        f"OUT={json.dumps(output_dir)}",
        'mkdir -p "$OUT"',
        "",
    ]
    by_candidate = {
        candidate["candidate_id"]: candidate
        for candidate in plan.get("candidates") or []
    }
    for eligible in evidence.get(
        "eligible_target_context_policies"
    ) or []:
        candidate = by_candidate[eligible["candidate_id"]]
        target_policy_id = eligible["target_policy_id"]
        target_profile_id = eligible["target_profile_id"]
        lines.extend(
            [
                (
                    "# === TARGET CONTEXT: "
                    + candidate["candidate_id"]
                    + " ==="
                ),
                (
                    "# "
                    + json.dumps(
                        candidate["signature"],
                        sort_keys=True,
                    )
                ),
            ]
        )
        for mode in ("stable_prefix", "growing_prefix"):
            stem = (
                candidate["candidate_id"]
                + ".target."
                + mode
            )
            lines.extend(
                [
                    "",
                    (
                        "# FRESH TARGET-CONTEXT RUNTIME REQUIRED before "
                        + mode
                    ),
                    (
                        "PYTHONPATH=src python "
                        "scripts/run_assistx_inference_session_soak.py "
                        f"--profiles {json.dumps(plan['profiles_file'])} "
                        f"--profile-id {json.dumps(target_profile_id)} "
                        f"--matrix {json.dumps(plan['matrix_file'])} "
                        f"--policy-id {json.dumps(target_policy_id)} "
                        f"--mode {json.dumps(mode)} "
                        f"--turns {int(plan['turns'])} "
                        f"--plan-out "$OUT/{stem}.plan.json" "
                        "--execute "
                        f"--results-out "$OUT/{stem}.results.jsonl" "
                        f"--summary-out "$OUT/{stem}.summary.json" "
                        f"--checkpoint-dir "$OUT/{candidate['candidate_id']}.target.checkpoints""
                    ),
                ]
            )
        lines.append("")
    if not evidence.get("eligible_target_context_policies"):
        lines.append(
            "# No source-context candidates were eligible for target-context execution."
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
    eval_parser.add_argument("--advance-commands-out")
    eval_parser.add_argument(
        "--evidence-dir",
        default="/tmp/assistx-soak-campaign-target",
    )

    target_parser = sub.add_parser("evaluate-target")
    target_parser.add_argument("--plan", required=True)
    target_parser.add_argument("--source-evidence", required=True)
    target_parser.add_argument(
        "--summaries",
        nargs="+",
        required=True,
    )
    target_parser.add_argument("--output", required=True)

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

    if args.command == "evaluate-target":
        source_evidence = _load_json(args.source_evidence)
        evidence = evaluate_target_context(
            plan,
            source_evidence,
            summaries,
        )
        _write_json(args.output, evidence)
        print(
            json.dumps(
                {
                    "campaign_id": evidence["campaign_id"],
                    "evaluated_target_candidates": len(
                        evidence["evaluated_target_candidates"]
                    ),
                    "benchmark_complete_target_context_policies": len(
                        evidence[
                            "benchmark_complete_target_context_policies"
                        ]
                    ),
                    "output": args.output,
                    "production_promotion_authorized": False,
                    "routing_authority_changed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    evidence = evaluate_campaign(plan, summaries)
    _write_json(args.output, evidence)
    if args.advance_commands_out:
        commands_path = Path(args.advance_commands_out)
        commands_path.parent.mkdir(parents=True, exist_ok=True)
        commands_path.write_text(
            _render_advance_commands(
                plan,
                evidence,
                args.evidence_dir,
            ),
            encoding="utf-8",
        )
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
                "advance_commands_out": args.advance_commands_out,
                "production_promotion_authorized": False,
                "routing_authority_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
