#!/usr/bin/env python3
"""Fixed, synthetic-only node-side Ed25519 grant verifier (grant on stdin).

Public signing key stays local to the target; signing private key stays on
x1-370. This *does not* consume or verify a real AssistX-issued claim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from assistx.trace_execution_adapter import TraceDenied, run_trace_probe
from assistx.trace_shadow_grant import public_key, verify_grant


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--audit-root", required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise TraceDenied("grant_payload_too_large")
        try:
            document = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise TraceDenied("invalid_grant_json") from exc
        grant_hash = verify_grant(
            document,
            node_id=args.node_id,
            verifier=public_key(args.public_key),
        )
        task = {
            "id": document["task_id"],
            "target_agent_id": document["node_id"],
            "payload": {"command_id": "probe.noop.v1"},
        }
        outcome = run_trace_probe(
            task,
            node_id=args.node_id,
            claim_id=document["claim_id"],
            audit_root=args.audit_root,
            enabled=True,
            signed_grant_sha256=grant_hash,
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "synthetic": True,
                    "assistx_claim_verified": False,
                    "signed_shadow_grant_verified": True,
                    "node_id": args.node_id,
                    "grant_sha256": grant_hash,
                    "prepared_hash": outcome["trace"]["prepared_hash"],
                    "completed_hash": outcome["trace"]["completed_hash"],
                },
                sort_keys=True,
            )
        )
        return 0
    except (TraceDenied, OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, TraceDenied) else "grant_validation_failed"
        print(
            json.dumps(
                {
                    "ok": False,
                    "synthetic": True,
                    "assistx_claim_verified": False,
                    "node_id": args.node_id,
                    "reason": reason,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
