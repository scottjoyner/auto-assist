#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from assistx.inference_task_evaluator_report import (
    load_task_evaluator_suite,
    summarize_task_evaluator_results,
)


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"{path}:{line_number}: expected JSON object"
                )
            rows.append(value)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize deterministic AssistX task-evaluator results by "
            "policy. This produces offline quality evidence only."
        )
    )
    parser.add_argument("--suite", required=True)
    parser.add_argument(
        "--results",
        nargs="+",
        required=True,
        help="One or more task-evaluator result JSONL files.",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    suite = load_task_evaluator_suite(args.suite)
    rows: list[dict[str, Any]] = []
    for result_path in args.results:
        rows.extend(_read_jsonl(result_path))
    report = summarize_task_evaluator_results(
        rows,
        suite,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    eligible = [
        row for row in report["policies"]
        if row["eligible_for_training_evidence"] is True
    ]
    print(
        json.dumps(
            {
                "suite_id": report["suite_id"],
                "suite_sha256": report["suite_sha256"],
                "policy_count": report["policy_count"],
                "eligible_for_training_evidence": len(eligible),
                "output": str(output),
                "production_promotion_authorized": False,
                "routing_authority_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
