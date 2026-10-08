#!/usr/bin/env python3
"""Synthetic-only trace journal canary. No network, shell, or live AssistX claim."""

from __future__ import annotations

import argparse
import json
import uuid

from assistx.trace_execution_adapter import TraceDenied, TraceReceiptStore, run_trace_probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", required=True, help="Existing, private mode-0700 directory")
    parser.add_argument("--node-id", required=True, help="Stable local node identity")
    parser.add_argument("--verify-only", action="store_true", help="Read-only SHA-256 chain verification")
    args = parser.parse_args()
    try:
        store = TraceReceiptStore(args.audit_root, node_id=args.node_id)
        if args.verify_only:
            print(json.dumps({"synthetic": True, **store.verify()}, sort_keys=True))
            return 0
        token = uuid.uuid4().hex
        outcome = run_trace_probe(
            {
                "id": f"synthetic-{token}",
                "target_agent_id": args.node_id,
                "task_type": "trace_probe",
                "payload": {"command_id": "probe.noop.v1"},
            },
            node_id=args.node_id,
            claim_id=f"synthetic-{token}",
            audit_root=args.audit_root,
            enabled=True,
        )
        print(
            json.dumps(
                {
                    "synthetic": True,
                    "assistx_claim_verified": False,
                    "command_id": "probe.noop.v1",
                    "status": outcome["status"],
                    "trace": outcome["trace"],
                    "verification": store.verify(),
                },
                sort_keys=True,
            )
        )
        return 0
    except TraceDenied as exc:
        print(json.dumps({"synthetic": True, "status": "DENIED", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
