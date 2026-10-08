#!/usr/bin/env python3
"""Two-node signed synthetic grant positive/replay/expiry/wrong-node drills.

Per selected node: ONE signed no-op succeeds, all signed grant negative cases
must deny without changing journal. No real command or AssistX work.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from trace_execution_shadow_backup import DEFAULT_PRIVATE
from trace_execution_shadow_control import load_paths, run
from trace_shadow_grant_control import dispatch

from assistx.trace_shadow_grant import new_grant, private_key


def drill(nodes: dict, node_id: str, secret_root: Path) -> dict:
    node = nodes[node_id]
    other_id = next(name for name in nodes if name != node_id)
    signer = private_key(secret_root / node_id / "grant-signing-key.pem")
    before = run(node, canary=False)
    if not before.get("ok"):
        raise RuntimeError("baseline_trace_verify_failed")
    baseline = before["remote"]["records"]
    fresh = new_grant(signer=signer, node_id=node_id)
    result = dispatch(node, fresh)
    if not result["accepted"]:
        raise RuntimeError("positive_synthetic_grant_denied")
    replay = dispatch(node, fresh)
    if replay["accepted"] or replay["reply"].get("reason") != "attempt_already_recorded":
        raise RuntimeError("replay_not_denied")
    expired = new_grant(signer=signer, node_id=node_id, now_ms=int(time.time() * 1000) - 60_000)
    expired_result = dispatch(node, expired)
    if expired_result["accepted"] or expired_result["reply"].get("reason") != "grant_expired":
        raise RuntimeError("expiration_not_enforced")
    other_signer = private_key(secret_root / other_id / "grant-signing-key.pem")
    wrong = dispatch(node, new_grant(signer=other_signer, node_id=other_id))
    if wrong["accepted"] or wrong["reply"].get("reason") != "wrong_execution_node":
        raise RuntimeError("node_binding_not_enforced")
    tampered = new_grant(signer=signer, node_id=node_id)
    tampered["expires_at_ms"] -= 1
    tampered_result = dispatch(node, tampered)
    if tampered_result["accepted"] or tampered_result["reply"].get("reason") != "grant_signature_invalid":
        raise RuntimeError("signature_integrity_not_enforced")
    after = run(node, canary=False)
    if not after.get("ok") or after["remote"]["records"] != baseline + 2:
        raise RuntimeError("negative_drill_modified_journal")
    return {
        "node_id": node_id,
        "ok": True,
        "synthetic_signed_grant_accepted": True,
        "replay_rejected": True,
        "expired_rejected": True,
        "wrong_node_rejected": True,
        "tampered_signature_rejected": True,
        "before_records": baseline,
        "after_records": after["remote"]["records"],
        "negative_attempts_added_no_receipts": True,
        "last_hash": after["remote"]["last_hash"],
        "assistx_claim_verified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--private", type=Path, default=DEFAULT_PRIVATE)
    args = parser.parse_args()
    nodes = load_paths(args.config)
    if args.node not in nodes:
        raise SystemExit("unregistered_shadow_node")
    result = drill(nodes, args.node, args.private)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
