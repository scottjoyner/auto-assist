#!/usr/bin/env python3
"""Read-only SSH verification or explicitly requested synthetic no-op per node.

Not a scheduler or an authorization mechanism. Never sends shell payloads,
credentials, inference requests, or real AssistX tasks.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path

NODE = re.compile(r"^[a-zA-Z0-9_.-]+$")
SSH_TARGET = re.compile(r"^(?:[a-zA-Z0-9_.-]+@)?[a-zA-Z0-9_.-]+$")
PATH = re.compile(r"^/[a-zA-Z0-9_./-]+$")


def load_paths(path: str) -> dict[str, dict[str, str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        data.get("schema") != "assistx.trace-execution-shadow-paths.v1"
        or data.get("mode") != "synthetic-only"
        or data.get("live_dispatch_enabled") is not False
    ):
        raise ValueError("shadow registry requires explicit non-live policy")
    records = data.get("nodes")
    if not isinstance(records, list) or len(records) != 2:
        raise ValueError("expected two separately configured shadow nodes")
    nodes = {}
    roots = set()
    for record in records:
        if not isinstance(record, dict) or record.get("production_worker_enabled") is not False:
            raise ValueError("live node must not enter shadow control")
        node_id = record.get("node_id")
        target = record.get("ssh_target")
        release = record.get("release_root")
        audit = record.get("audit_root")
        python = record.get("python")
        if (
            not isinstance(node_id, str)
            or not NODE.fullmatch(node_id)
            or not isinstance(target, str)
            or not SSH_TARGET.fullmatch(target)
            or not isinstance(release, str)
            or not PATH.fullmatch(release)
            or not isinstance(audit, str)
            or not PATH.fullmatch(audit)
            or python != "python3"
        ):
            raise ValueError("invalid path or identity in registry")
        if node_id in nodes or audit in roots or release in roots:
            raise ValueError("duplicate node identity or execution path")
        if ".." in Path(release).parts or ".." in Path(audit).parts:
            raise ValueError("parent path traversal is not allowed")
        if not release.startswith(("/home/", "/Users/")) or not audit.startswith(("/home/", "/Users/")):
            raise ValueError("shadow paths must be user-owned")
        if "/assistx-trace-shadow/" not in release or "/trace-execution-shadow-" not in audit:
            raise ValueError("not an isolated shadow directory")
        roots.update((release, audit))
        nodes[node_id] = record
    return nodes


def run(node: dict[str, str], *, canary: bool, negative: bool = False) -> dict:
    # This is a fixed synthetic-only command constructed from a validated
    # registry. ssh itself is passed an argv array with host-key checking.
    script = "trace_execution_shadow_negative.py" if negative else "trace_execution_canary.py"
    remote = [
        "env",
        "PYTHONPATH=" + node["release_root"] + "/src",
        "python3",
        node["release_root"] + "/scripts/" + script,
        "--node-id",
        node["node_id"],
        "--audit-root",
        node["audit_root"],
    ]
    if not canary and not negative:
        remote.append("--verify-only")
    remote_command = " ".join(shlex.quote(item) for item in remote)
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
        node["ssh_target"],
        remote_command,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=25, check=False)
    if result.returncode != 0:
        return {
            "node_id": node["node_id"],
            "ok": False,
            "exit_code": result.returncode,
            "reason": "remote_probe_failed",
            "stderr": result.stderr.strip()[-240:],
        }
    try:
        body = json.loads(result.stdout)
    except ValueError:
        return {"node_id": node["node_id"], "ok": False, "reason": "invalid_remote_reply"}
    return {
        "node_id": node["node_id"],
        "ok": body.get("synthetic") is True and body.get("verification", body).get("ok") is True,
        "mode": "negative_checks" if negative else "synthetic_canary" if canary else "verify_only",
        "remote": body,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--node", required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--verify-only", action="store_true")
    modes.add_argument("--run-synthetic-noop", action="store_true")
    modes.add_argument("--run-negative-checks", action="store_true")
    args = parser.parse_args()
    try:
        nodes = load_paths(args.config)
        if args.node not in nodes:
            raise ValueError("unregistered_node")
        result = run(
            nodes[args.node],
            canary=args.run_synthetic_noop,
            negative=args.run_negative_checks,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"node_id": args.node, "ok": False, "reason": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
