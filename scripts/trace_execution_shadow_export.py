#!/usr/bin/env python3
"""Read-only, locked shadow journal export; JSON/base64 over authenticated SSH."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json

from assistx.trace_execution_adapter import TraceDenied, TraceReceiptStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--audit-root", required=True)
    args = parser.parse_args()
    try:
        store = TraceReceiptStore(args.audit_root, node_id=args.node_id)
        raw = store.snapshot()
        records = TraceReceiptStore._verify_data(raw)
        envelope = {
            "schema": "assistx.trace-shadow-export.v1",
            "node_id": args.node_id,
            "records": len(records),
            "last_hash": records[-1]["entry_hash"],
            "journal_sha256": hashlib.sha256(raw).hexdigest(),
            "journal_base64": base64.b64encode(raw).decode("ascii"),
        }
        print(json.dumps(envelope, sort_keys=True))
        return 0
    except TraceDenied as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
