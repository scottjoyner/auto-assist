#!/usr/bin/env python3
"""Guarded single-instance real Redis 1/3/5/10 concurrent admission experiment.

No host ports, external network, production credentials, graph or provider.
Creates only an internal-only Docker network, scratch Redis and read-only client.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
NET = "assistx-redis-mw-internal-20261009"
SERVER = "assistx-redis-mw-fence-isolated-20261009"
CLIENT = "assistx-redis-mw-client-isolated-20261009"
SCRIPT = ROOT / "scripts/trace_real_redis_multiworker_client.py"
IMAGES = {"redis": "redis:7-alpine", "client": "git-assistx:latest"}
SHA = re.compile(r"^sha256:[a-f0-9]{64}$")
HEX = re.compile(r"^[a-f0-9]{40}$")
ID = re.compile(r"^[a-f0-9]{64}$")


def cmd(*args: str, timeout: int = 16) -> str:
    r = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if r.returncode != 0:
        raise RuntimeError("disposable Docker/Git command failed")
    return r.stdout.decode("utf-8", "replace").strip()


def exists(kind: str, name: str) -> bool:
    p = subprocess.run(["docker", kind, "inspect", name],
                       capture_output=True, timeout=7, check=False)
    return p.returncode == 0


def witness_network(nid: str, expected: set[str]) -> None:
    state = json.loads(cmd("docker", "network", "inspect", nid))[0]
    peers = {item.get("Name") for item in
             (state.get("Containers") or {}).values()}
    if (state.get("Id") != nid or state.get("Name") != NET
            or state.get("Internal") is not True
            or state.get("Attachable") is True or state.get("Ingress") is True
            or peers != expected):
        raise RuntimeError("disposable network identity or membership changed")


def witness_container(cid: str, name: str, image: str) -> None:
    state = json.loads(cmd("docker", "inspect", "--type", "container", cid))[0]
    host = state.get("HostConfig") or {}
    if (state.get("Id") != cid or state.get("Name") != "/" + name
            or state.get("Image") != image or not state.get("State", {}).get("Running")
            or host.get("NetworkMode") != NET or bool(host.get("PortBindings"))
            or bool(host.get("Binds")) or bool(host.get("Privileged"))
            or host.get("ReadonlyRootfs") is not True):
        raise RuntimeError("untrusted disposable container topology")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--approve-disposable-multiworker", action="store_true")
    p.add_argument("--expected-source-sha", required=True)
    p.add_argument("--redis-image-id", required=True)
    p.add_argument("--client-image-id", required=True)
    a = p.parse_args()
    images = {"redis": a.redis_image_id, "client": a.client_image_id}
    if (not a.approve_disposable_multiworker
            or not isinstance(a.expected_source_sha, str)
            or not HEX.fullmatch(a.expected_source_sha)
            or not all(isinstance(v, str) and SHA.fullmatch(v)
                       for v in images.values())):
        print("HOLD: explicit opt-in and exact image/source IDs required")
        return 2
    try:
        if cmd("git", "-C", str(ROOT), "rev-parse", "HEAD") != a.expected_source_sha:
            raise RuntimeError("source revision changed")
        if cmd("git", "-C", str(ROOT), "status", "--porcelain"):
            raise RuntimeError("dirty source")
        if not SCRIPT.is_file() or not (ROOT / "src" / "assistx").is_dir():
            raise RuntimeError("research source incomplete")
        for role, label in IMAGES.items():
            if cmd("docker", "image", "inspect", label, "--format",
                   "{{.Id}}") != images[role]:
                raise RuntimeError("Docker image identity changed")
        if exists("network", NET) or any(exists("container", x)
                                        for x in (SERVER, CLIENT)):
            raise RuntimeError("pre-existing Docker object; refuse reuse")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print("HOLD: source/ownership preflight denied")
        return 2

    nid = None
    owned: list[str] = []
    phase = "network_creation"
    try:
        nid = cmd("docker", "network", "create", "--internal", NET)
        if not ID.fullmatch(nid):
            raise RuntimeError("invalid disposable network ID")
        witness_network(nid, set())
        phase = "redis_start"
        redis_id = cmd(
            "docker", "run", "--rm", "-d", "--pull", "never",
            "--name", SERVER, "--network", NET, "--read-only",
            "--tmpfs", "/data:rw,size=16m,mode=1777",
            "--tmpfs", "/tmp:rw,size=8m,mode=1777",
            "--user", "999:999", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--cpus", "0.25", "--memory", "128m",
            "--pids-limit", "32", images["redis"],
            "redis-server", "--save", "", "--appendonly", "no",
        )
        if not ID.fullmatch(redis_id):
            raise RuntimeError("malformed scratch Redis ID")
        owned.append(redis_id)
        witness_container(redis_id, SERVER, images["redis"])
        witness_network(nid, {SERVER})
        phase = "client_run"
        client_id = cmd(
            "docker", "run", "-d", "--pull", "never",
            "--name", CLIENT, "--network", NET, "--read-only",
            "--tmpfs", "/tmp:rw,size=16m,mode=1777",
            "--user", "1000:1000", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--cpus", "0.5", "--memory", "512m",
            "--pids-limit", "64",
            "--mount", f"type=bind,source={ROOT / 'src'},target=/work/src,readonly",
            "--mount", f"type=bind,source={SCRIPT},target=/work/client.py,readonly",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "ASSISTX_DISPOSABLE_REDIS_MULTIWORKER=synthetic-explicit-opt-in",
            "-e", f"ASSISTX_TEST_REDIS_HOST={SERVER}",
            "--entrypoint", "python", images["client"], "/work/client.py",
        )
        if not ID.fullmatch(client_id):
            raise RuntimeError("malformed scratch client ID")
        owned.append(client_id)
        witness_container(client_id, CLIENT, images["client"])
        witness_network(nid, {SERVER, CLIENT})
        phase = "collect_physical_witness"
        result = cmd("docker", "wait", client_id, timeout=45)
        log = cmd("docker", "logs", client_id, timeout=10)
        required = "REAL_REDIS_1_3_5_10_ATOMIC_CAP_AND_RELEASE_PASS"
        if result != "0" or required not in log:
            raise RuntimeError("real Redis contention acceptance not witnessed")
        for size, admitted, denied in ((1,1,0),(3,3,0),(5,3,2),(10,3,7)):
            line = f"REAL_REDIS_CONCURRENT_ADMISSION {size} {admitted} {denied} {admitted}"
            if line not in log:
                raise RuntimeError("real Redis batch result mismatch")
        print("REAL_REDIS_1_3_5_10_ATOMIC_CAP_AND_RELEASE_PASS")
        print("NO_REAL_GRAPH_OR_PROVIDER_USED")
        return 0
    except (OSError, RuntimeError, ValueError, KeyError,
            subprocess.SubprocessError):
        print("DISPOSABLE_MULTIWORKER_FAIL_OR_INCONCLUSIVE")
        print("FAILED_PHASE", phase)
        return 1
    finally:
        for cid in reversed(owned):
            if ID.fullmatch(cid):
                subprocess.run(["docker", "stop", "-t", "1", cid],
                               capture_output=True, timeout=9, check=False)
                # The client is retained for exit/log evidence before cleanup.
                subprocess.run(["docker", "rm", "-f", cid],
                               capture_output=True, timeout=9, check=False)
        if nid and ID.fullmatch(nid):
            subprocess.run(["docker", "network", "rm", nid],
                           capture_output=True, timeout=9, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
