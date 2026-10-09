#!/usr/bin/env python3
"""Deny-only evidence evaluation; NEVER authorizes production NAS custody.

Inputs are sanitized observations collected separately on server/client.
An operator must authenticate their provenance before any real-write drill.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assistx.trace_smb_custody_readiness import evaluate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-evidence", type=Path, required=True)
    parser.add_argument("--client-evidence", type=Path, required=True)
    parser.add_argument("--expected-server-hostname", required=True)
    parser.add_argument("--expected-server-uuid", required=True)
    parser.add_argument("--expected-server-mount", required=True)
    parser.add_argument("--expected-share", required=True)
    parser.add_argument("--expected-server-path", required=True)
    parser.add_argument("--expected-client-source", required=True)
    parser.add_argument("--expected-client-target", required=True)
    parser.add_argument("--dedicated-user", required=True)
    args = parser.parse_args()
    try:
        server = json.loads(args.server_evidence.read_text())
        client = json.loads(args.client_evidence.read_text())
        report = evaluate(
            {"server": server, "client": client},
            expected_server_hostname=args.expected_server_hostname,
            expected_server_uuid=args.expected_server_uuid,
            expected_server_mount=args.expected_server_mount,
            expected_share=args.expected_share,
            expected_server_path=args.expected_server_path,
            expected_client_source=args.expected_client_source,
            expected_client_target=args.expected_client_target,
            dedicated_user=args.dedicated_user,
        )
    except (ValueError, TypeError, OSError):
        report = {
            "schema": "assistx.trace-smb-readiness.v1",
            "eligible_for_bounded_write_pilot": False,
            "production_custody_approved": False,
            "reason_codes": ["EVIDENCE_LOAD_FAILED"],
        }
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["eligible_for_bounded_write_pilot"] else 2


if __name__ == "__main__":
    sys.exit(main())
