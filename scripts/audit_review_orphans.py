#!/usr/bin/env python3
"""Explicit, bounded and read-only review provenance inspection.

Research only. Never runs as an automatic job or agent hook. Prints aggregate
counts, never Task IDs or environment credentials; exit 0 means the audit
completed, NOT that Task authority or production release is approved.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy

from neo4j import GraphDatabase


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only-audit",action="store_true",
        help="Explicit acknowledgement of bounded READ_ACCESS inspection",
    )
    parser.add_argument("--limit",type=int,default=150)
    args = parser.parse_args(argv)
    if not args.read_only_audit:
        parser.error("--read-only-audit is required; no automatic runs")
    if not 1 <= args.limit <= 300:
        parser.error("--limit must be between 1 and 300")
    needed=("NEO4J_URI","NEO4J_USER","NEO4J_PASSWORD")
    if any(not os.getenv(k) for k in needed):
        # No secrets, URIs, paths or IDs are included in output.
        print(json.dumps({"decision":"HOLD","error_type":"missing_connection_configuration"}))
        return 2
    module=runpy.run_path(str(
        Path(__file__).resolve().parents[1] /
        "src" / "assistx" / "review_orphan_audit.py"
    ))
    driver=None
    try:
        driver=GraphDatabase.driver(
            os.environ["NEO4J_URI"],
            auth=(os.environ["NEO4J_USER"],os.environ["NEO4J_PASSWORD"]),
        )
        report=module["audit_review_provenance"](
            driver,
            database=os.getenv("NEO4J_DATABASE") or "neo4j",
            limit=args.limit,
        )
    except Exception as exc:
        # NEVER show exception text: driver errors may contain target URI,
        # query params, metadata, or authentication details.
        print(json.dumps({"decision":"HOLD","error_type":type(exc).__name__}))
        return 2
    finally:
        if driver is not None:
            driver.close()
    print(json.dumps(report,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
