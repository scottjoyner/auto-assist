#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from assistx.neo4j_backup_verification import (
    BackupVerificationError,
    Neo4jBackupVerifier,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect a Neo4j Enterprise backup chain and optionally run consistency checks.",
    )
    parser.add_argument("path")
    parser.add_argument("--database", default="neo4j")
    parser.add_argument("--max-age-seconds", type=int, default=3600)
    parser.add_argument("--require-recovered", action="store_true")
    parser.add_argument("--consistency-check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    verifier = Neo4jBackupVerifier()
    try:
        result = verifier.verify(
            args.path,
            args.database,
            max_age_seconds=args.max_age_seconds,
            require_recovered=args.require_recovered,
        )
        if args.consistency_check:
            result["consistency"] = verifier.run_consistency_check(
                args.path,
                args.database,
            )
    except BackupVerificationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
