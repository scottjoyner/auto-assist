#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

from assistx.recovery_snapshot import RecoverySnapshotReplicator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replicate a signed AssistX runtime projection to the warm recovery island.",
    )
    parser.add_argument(
        "--source-url",
        default=os.getenv("RECOVERY_SNAPSHOT_SOURCE_URL", ""),
    )
    parser.add_argument(
        "--target-url",
        default=os.getenv("RECOVERY_SNAPSHOT_TARGET_URL", "http://127.0.0.1:27900"),
    )
    parser.add_argument(
        "--snapshot-path",
        default=os.getenv(
            "RECOVERY_SNAPSHOT_PATH",
            "/var/lib/assistx-recovery/state/runtime-projection.json",
        ),
    )
    return parser


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def main() -> int:
    args = build_parser().parse_args()
    try:
        replicator = RecoverySnapshotReplicator(
            source_url=args.source_url,
            target_url=args.target_url,
            source_auth=(
                required("RECOVERY_SNAPSHOT_SOURCE_USER"),
                required("RECOVERY_SNAPSHOT_SOURCE_PASS"),
            ),
            target_auth=(
                required("RECOVERY_SNAPSHOT_TARGET_USER"),
                required("RECOVERY_SNAPSHOT_TARGET_PASS"),
            ),
            secret=required("ASSISTX_RUNTIME_PROJECTION_HMAC_SECRET"),
            snapshot_path=args.snapshot_path,
        )
        result = replicator.replicate()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
