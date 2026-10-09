#!/usr/bin/env python3
"""Explicit, bounded READ-ONLY cancellation canary; no live fleet credentials.

The operator must supply exact Git revision + immutable Docker image SHAs.
The only allowed test database is in a known internal-only Docker network with
no published ports and disposable temp data. The runner never provisions,
modifies or restarts Neo4j, Redis, production services, or other containers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

NETWORK = "assistx-neo526-internal-study-20261009"
SERVER = "assistx-neo526-physical-cancel-20261009"
URI = f"bolt://{SERVER}:7687"
HEX = re.compile(r"^[0-9a-f]{40}$")
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    ("direct", "trace_async_neo4j_cancel_canary.py", "REAL_NEO4J_ASYNC_CANCELLATION_PASS"),
    ("lease-loss", "trace_fenced_adapter_real_neo4j_canary.py", "LEASE_LOSS_REAL_NEO4J_CANCELLATION_PASS"),
)


def verify_disposable_topology(net: dict, server: dict, approved_db_image: str) -> None:
    if net.get("Name") != NETWORK or net.get("Internal") is not True:
        raise ValueError("Neo4j test network is not the approved internal-only network")
    if net.get("Attachable") is True or net.get("Ingress") is True:
        raise ValueError("Neo4j test network must be non-attachable, non-ingress")
    containers = list((net.get("Containers") or {}).values())
    if len(containers) != 1 or containers[0].get("Name") != SERVER:
        raise ValueError("Neo4j test network has unexpected peers")
    if server.get("Name") != "/" + SERVER or server.get("Image") != approved_db_image:
        raise ValueError("test server identity or image has changed")
    if server.get("State", {}).get("Running") is not True:
        raise ValueError("isolated Neo4j is not running")
    config = server.get("HostConfig", {})
    if config.get("NetworkMode") != NETWORK or config.get("PortBindings"):
        raise ValueError("test Neo4j must not publish any host ports")
    if config.get("Binds") or server.get("Mounts"):
        raise ValueError("unexpected mounted volumes in disposable database")
    tmpfs = config.get("Tmpfs") or {}
    if not all(name in tmpfs for name in ("/data", "/logs", "/tmp")):
        raise ValueError("disposable database directories are not ephemeral")
    if config.get("Memory", 0) > 2 * 1024 ** 3 or config.get("NanoCpus", 0) > 750_000_000:
        raise ValueError("database exceeds the approved research resource limit")
    # We do not print environment contents. This simply rejects a non-synthetic DB.
    env = server.get("Config", {}).get("Env") or []
    if "NEO4J_AUTH=none" not in env or "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes" not in env:
        raise ValueError("Neo4j instance is not the synthetic no-credential setup")


def client_command(image: str, script: str) -> list[str]:
    if not SHA.fullmatch(image):
        raise ValueError("client Docker image must be addressed by immutable SHA256")
    if script not in {item[1] for item in SCRIPTS}:
        raise ValueError("unapproved source canary")
    if not (ROOT / "src" / "assistx").is_dir() or not (ROOT / "scripts" / script).is_file():
        raise ValueError("canary source tree incomplete")
    mode = ("ASSISTX_ISOLATED_NEO4J_CANCEL_CANARY=synthetic-explicit-opt-in"
            if script == SCRIPTS[0][1]
            else "ASSISTX_ISOLATED_FENCED_NEO4J_CANARY=synthetic-explicit-opt-in")
    return [
        "docker", "run", "--rm", "--pull", "never", "--network", NETWORK,
        "--ipc", "none", "--cpus", "0.4", "--memory", "512m", "--pids-limit", "48",
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=16m",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=bind,source={ROOT / 'src'},target=/work/src,readonly",
        "--mount", f"type=bind,source={ROOT / 'scripts' / script},target=/work/canary.py,readonly",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONPATH=/work/src",
        "-e", mode,
        "-e", f"ASSISTX_TEST_TARGET_URI={URI}",
        "--entrypoint", "/usr/local/bin/python", image, "/work/canary.py",
    ]


def _checked_output(args: list[str], timeout: float) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-isolated-cancellation", action="store_true")
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--expected-client-image-id")
    parser.add_argument("--expected-neo4j-image-id")
    args = parser.parse_args(argv)
    if not args.approve_isolated_cancellation:
        print("BLOCKED: explicit operator opt-in required", file=sys.stderr)
        return 2
    if (not isinstance(args.expected_source_sha, str) or not HEX.fullmatch(args.expected_source_sha)
            or not isinstance(args.expected_client_image_id, str) or not SHA.fullmatch(args.expected_client_image_id)
            or not isinstance(args.expected_neo4j_image_id, str) or not SHA.fullmatch(args.expected_neo4j_image_id)):
        print("BLOCKED: exact Git and client/DB Docker SHAs are required", file=sys.stderr)
        return 2
    try:
        source = _checked_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], 8)
        if source != args.expected_source_sha or _checked_output(
            ["git", "-C", str(ROOT), "status", "--porcelain"], 8):
            raise ValueError("canary source is uncommitted or not the approved Git revision")
        for image in (args.expected_client_image_id, args.expected_neo4j_image_id):
            local_id = _checked_output(["docker", "image", "inspect", image,
                                        "--format", "{{.Id}}"], 10)
            if local_id != image:
                raise ValueError("Docker image digest does not match approved value")
        net = json.loads(_checked_output(["docker", "network", "inspect", NETWORK], 10))[0]
        server = json.loads(_checked_output(["docker", "inspect", SERVER], 10))[0]
        verify_disposable_topology(net, server, args.expected_neo4j_image_id)
        for label, script, marker in SCRIPTS:
            result = subprocess.run(client_command(args.expected_client_image_id, script),
                                    capture_output=True, text=True, timeout=30)
            if result.returncode != 0 or marker not in result.stdout:
                print("FAILED isolated " + label + " canary (no production effects)", file=sys.stderr)
                print((result.stdout + "\n" + result.stderr)[-700:], file=sys.stderr)
                return 1
            print("PASS", label, marker)
        print("FENCED_ASYNC_NEO4J_CANCEL_RESEARCH_PASS")
        return 0
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
        print("BLOCKED/FAILED: " + str(exc)[:180], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
