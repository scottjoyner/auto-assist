#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from assistx.inference_policy_training_dataset import (
    DEFAULT_SPLIT_FRACTIONS,
    build_policy_training_bundle,
)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze quality-gated AssistX inference evidence into a "
            "deterministic my-jev policy-decision training bundle."
        )
    )
    parser.add_argument("--cases", required=True)
    parser.add_argument("--matrix", required=True)
    parser.add_argument(
        "--results",
        nargs="+",
        required=True,
        help="One or more source/target quality replay result JSONL files.",
    )
    parser.add_argument("--source-campaign-evidence", required=True)
    parser.add_argument("--target-campaign-evidence", required=True)
    parser.add_argument("--source-quality-evidence", required=True)
    parser.add_argument("--target-quality-evidence", required=True)
    parser.add_argument("--producer-git-sha", required=True)
    parser.add_argument(
        "--split-seed",
        default="assistx-policy-training-v1",
    )
    parser.add_argument(
        "--tie-ratio",
        type=float,
        default=1.03,
        help=(
            "Policies within this multiplicative latency ratio of the "
            "best accepted policy share target probability mass."
        ),
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(
            f"output directory must be empty: {output}"
        )

    bundle = build_policy_training_bundle(
        cases_file=args.cases,
        matrix_file=args.matrix,
        result_files=args.results,
        source_campaign_evidence_file=args.source_campaign_evidence,
        target_campaign_evidence_file=args.target_campaign_evidence,
        source_quality_report_file=args.source_quality_evidence,
        target_quality_report_file=args.target_quality_evidence,
        producer_git_sha=args.producer_git_sha,
        split_seed=args.split_seed,
        tie_ratio=args.tie_ratio,
    )
    records = bundle["records"]
    manifest = bundle["manifest"]

    _write_json(output / "manifest.json", manifest)
    _write_jsonl(output / "records.jsonl", records)
    for split in DEFAULT_SPLIT_FRACTIONS:
        _write_jsonl(
            output / f"{split}.jsonl",
            [row for row in records if row["split"] == split],
        )

    receipt = {
        "schema": "assistx-policy-training-export-receipt-v1",
        "bundle_sha256": manifest["bundle_sha256"],
        "records_sha256": manifest["records_sha256"],
        "record_count": manifest["record_count"],
        "split_counts": manifest["split"]["record_counts"],
        "output_dir": str(output),
        "evidence_only": True,
        "dispatch_allowed": False,
        "production_promotion_authorized": False,
        "routing_authority_changed": False,
    }
    _write_json(output / "receipt.json", receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
