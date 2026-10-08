#!/usr/bin/env python3
"""Explicit node selection for authenticated *synthetic-only* shadow no-op.

Signs grant on x1-370 with node-private Ed25519; sends JSON to fixed node
verifier over existing SSH. This is NOT AssistX task issuance or a scheduler.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path

from trace_execution_shadow_backup import DEFAULT_PRIVATE
from trace_execution_shadow_control import load_paths

from assistx.trace_shadow_grant import new_grant, private_key


def dispatch(node: dict, grant: dict) -> dict:
    args = [
        "env",
        "PYTHONPATH=" + node["release_root"] + "/src",
        "python3",
        node["release_root"] + "/scripts/trace_shadow_grant_node.py",
        "--node-id",
        node["node_id"],
        "--audit-root",
        node["audit_root"],
        "--public-key",
        node["release_root"] + "/config/grant-authority.pem",
    ]
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
        node["ssh_target"],
        " ".join(shlex.quote(value) for value in args),
    ]
    payload = json.dumps(grant, separators=(",", ":"), sort_keys=True).encode("ascii")
    if len(payload) > 4096:
        raise ValueError("grant_payload_too_large")
    completed = subprocess.run(
        command,
        input=payload,
        capture_output=True,
        timeout=25,
        check=False,
    )
    try:
        answer = json.loads(completed.stdout)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError("remote_shadow_grant_reply_invalid") from exc
    if (
        not isinstance(answer, dict)
        or answer.get("node_id") != node["node_id"]
        or answer.get("synthetic") is not True
        or answer.get("assistx_claim_verified") is not False
    ):
        raise RuntimeError("remote_shadow_grant_reply_mismatched")
    return {
        "node_id": node["node_id"],
        "exit_code": completed.returncode,
        "accepted": completed.returncode == 0 and answer.get("ok") is True,
        "reply": answer,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--private", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--run-synthetic-noop", action="store_true", required=True)
    args = parser.parse_args()
    nodes = load_paths(args.config)
    if args.node not in nodes:
        raise SystemExit("unregistered_shadow_node")
    signer = private_key(args.private / args.node / "grant-signing-key.pem")
    result = dispatch(nodes[args.node], new_grant(signer=signer, node_id=args.node))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
