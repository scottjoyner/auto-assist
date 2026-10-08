#!/usr/bin/env python3
"""Read-only negative drills against an existing synthetic no-op journal.

Tries duplicate and wrong-node synthetic probes, expects both to be denied,
and requires zero journal changes. No real command, network, or shell execution.
"""

from __future__ import annotations

import argparse
import json
import os
import stat

from assistx.trace_execution_adapter import TraceDenied, TraceReceiptStore, run_trace_probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--audit-root", required=True)
    args = parser.parse_args()
    try:
        store = TraceReceiptStore(args.audit_root, node_id=args.node_id)
        before = store.verify()
        if before["records"] < 2:
            raise TraceDenied("synthetic_journal_not_initialized")
        with open(store.path, encoding="utf-8") as stream:
            rows = [json.loads(x) for x in stream]
        first = next((x for x in rows if x.get("event") == "prepared" and x.get("command_id") == "probe.noop.v1"), None)
        if first is None:
            raise TraceDenied("synthetic_preparation_missing")
        duplicate = {
            "id": first["task_id"],
            "target_agent_id": args.node_id,
            "payload": {"command_id": "probe.noop.v1"},
        }
        wrong_target = {
            "id": "negative-wrong-node",
            "target_agent_id": "other-node",
            "payload": {"command_id": "probe.noop.v1"},
        }
        reasons = []
        for task, claim in (
            (duplicate, first["claim_id"]),
            (wrong_target, "negative-wrong-node-claim"),
        ):
            try:
                run_trace_probe(
                    task,
                    node_id=args.node_id,
                    claim_id=claim,
                    audit_root=args.audit_root,
                    enabled=True,
                )
            except TraceDenied as exc:
                reasons.append(str(exc))
            else:
                raise TraceDenied("negative_probe_was_executed")
        after = store.verify()
        root_mode = stat.S_IMODE(os.stat(store.root).st_mode)
        file_mode = stat.S_IMODE(os.stat(store.path).st_mode)
        ok = (
            reasons == ["attempt_already_recorded", "wrong_execution_node"]
            and before == after
            and root_mode == 0o700
            and file_mode == 0o600
        )
        print(
            json.dumps(
                {
                    "synthetic": True,
                    "ok": ok,
                    "node_id": args.node_id,
                    "duplicate_rejected": reasons[0] == "attempt_already_recorded",
                    "wrong_node_rejected": reasons[1] == "wrong_execution_node",
                    "journal_unchanged": before == after,
                    "records": after["records"],
                    "last_hash": after["last_hash"],
                    "private_modes": root_mode == 0o700 and file_mode == 0o600,
                },
                sort_keys=True,
            )
        )
        return 0 if ok else 2
    except (TraceDenied, OSError, ValueError, StopIteration) as exc:
        print(json.dumps({"synthetic": True, "ok": False, "reason": type(exc).__name__ + ": " + str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
