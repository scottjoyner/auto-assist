#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from assistx.inference_session_soak import compare_soak_summaries


def _load(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare completed AssistX long-session soak summaries. "
            "This is evidence-only and never changes routing."
        )
    )
    parser.add_argument(
        "summaries",
        nargs="+",
        help="Completed soak summary JSON files.",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = compare_soak_summaries(
        [_load(path) for path in args.summaries]
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
    print(
        json.dumps(
            {
                "output": str(output),
                "summaries": len(report["summaries"]),
                "stable_growing_pairs": len(
                    report["stable_growing_pairs"]
                ),
                "routing_authority_changed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
