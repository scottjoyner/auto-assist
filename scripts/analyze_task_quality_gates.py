#!/usr/bin/env python3
"""Report what different task-quality gate policies would conclude.

The shipped gate is exhaustive: every policy must pass every case, and the
suite's ``required_case_ids`` must equal the cases file. That is a strong
safety property and it does not scale -- one hard case makes a policy
ineligible and the whole bundle disappears. This tool takes real result files
and prints, side by side:

* the exhaustive verdict (what ships today),
* per-policy pass rates and the exact failing cases,
* what a *sampled* gate would have concluded at several sample sizes and
  thresholds, including how many cases the sample would have needed to run.

It changes nothing. It exists so the choice between those gates is made with
measurements instead of intuition.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

SAMPLE_SIZES = (8, 16, 32, 64)
THRESHOLDS = (1.0, 0.95, 0.9, 0.8)


def _rows(paths: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", nargs="+", required=True)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--output")
    args = parser.parse_args()

    rows = _rows(args.results)
    by_policy: dict[str, dict[str, bool]] = {}
    for row in rows:
        policy = str(row.get("policy_id"))
        by_policy.setdefault(policy, {})[str(row.get("case_id"))] = bool(
            row.get("acceptance_passed")
        )

    report: dict[str, Any] = {
        "schema": "assistx-task-quality-gate-analysis-v1",
        "result_files": len(args.results),
        "rows": len(rows),
        "policies": {},
        "exhaustive_gate": {},
        "sampled_gate": [],
    }

    exhaustive_pass = True
    for policy, results in sorted(by_policy.items()):
        failures = sorted(cid for cid, ok in results.items() if not ok)
        rate = 1.0 - (len(failures) / len(results) if results else 1.0)
        if failures:
            exhaustive_pass = False
        report["policies"][policy] = {
            "cases": len(results),
            "pass_rate": round(rate, 4),
            "failed_case_ids": failures,
            "exhaustive_eligible": not failures,
        }
    report["exhaustive_gate"] = {
        "every_policy_passes_every_case": exhaustive_pass,
        "eligible_policies": sorted(
            p for p, v in report["policies"].items() if v["exhaustive_eligible"]
        ),
    }

    rng = random.Random(args.seed)
    for size in SAMPLE_SIZES:
        for threshold in THRESHOLDS:
            verdicts: dict[str, bool] = {}
            for policy, results in by_policy.items():
                case_ids = sorted(results)
                if not case_ids:
                    verdicts[policy] = False
                    continue
                sample = case_ids if len(case_ids) <= size else rng.sample(case_ids, size)
                passed = sum(1 for cid in sample if results[cid])
                verdicts[policy] = (passed / len(sample)) >= threshold
            report["sampled_gate"].append(
                {
                    "sample_size": size,
                    "threshold": threshold,
                    "eligible_policies": sorted(p for p, ok in verdicts.items() if ok),
                    "eligible_count": sum(verdicts.values()),
                }
            )

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
