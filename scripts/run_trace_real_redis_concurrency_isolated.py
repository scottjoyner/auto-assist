#!/usr/bin/env python3
"""Explicit, bounded real Redis 1/3/5/10 contention in disposable Docker only.

No production Redis, Neo4j, graph data, environment credentials or API routes.
Neither network nor container names may exist beforehand.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
NETWORK = "assistx-trace-contention-internal-20261009"
REDIS = "assistx-trace-contention-redis-20261009"
CLIENT = "assistx-trace-contention-client-20261009"
SOURCE = ROOT / "scripts/trace_real_redis_concurrency_client.py"
REDIS_IMAGE = "redis:7-alpine"
CLIENT_IMAGE = "git-assistx:latest"
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
CID = re.compile(r"^[a-f0-9]{64}$")


def run(*args: str, timeout: int = 16) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, timeout=timeout, check=False)


def output(*args: str, timeout: int = 16) -> str:
    p = run(*args, timeout=timeout)
    if p.returncode:
        raise RuntimeError("isolated Docker or Git command unavailable")
    return p.stdout.decode("utf-8", "replace").strip()


def absent(kind: str, name: str) -> bool:
    return run("docker", kind, "inspect", name, timeout=8).returncode != 0


def network_exact(identity: str, members: set[str]) -> None:
    net = json.loads(output("docker", "network", "inspect", identity))[0]
    if (net.get("Id") != identity or net.get("Name") != NETWORK
            or net.get("Internal") is not True or net.get("Attachable") is True
            or net.get("Ingress") is True):
        raise RuntimeError("disposable network fence broken")
    peers = {v.get("Name") for v in (net.get("Containers") or {}).values()}
    if peers != members:
        raise RuntimeError("unexpected network peer")


def container_exact(cid: str, name: str, image: str) -> None:
    item = json.loads(output("docker", "inspect", cid))[0]
    host = item.get("HostConfig") or {}
    if (item.get("Id") != cid or item.get("Name") != "/" + name
            or item.get("Image") != image
            or host.get("NetworkMode") != NETWORK
            or bool(host.get("PortBindings")) or bool(host.get("Binds"))
            or bool(item.get("Mounts")) or not host.get("ReadonlyRootfs")
            or host.get("Privileged")):
        raise RuntimeError("disposable container fence broken")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--approve-isolated-redis-contention", action="store_true")
    ap.add_argument("--expected-source-sha", required=True)
    ap.add_argument("--redis-image-id", required=True)
    ap.add_argument("--client-image-id", required=True)
    args = ap.parse_args(argv)
    if (not args.approve_isolated_redis_contention
            or not isinstance(args.expected_source_sha, str)
            or not GIT_SHA.fullmatch(args.expected_source_sha)
            or not isinstance(args.redis_image_id, str)
            or not SHA.fullmatch(args.redis_image_id)
            or not isinstance(args.client_image_id, str)
            or not SHA.fullmatch(args.client_image_id)):
        print("HOLD: explicit approval and immutable source/images required")
        return 2
    try:
        if output("git", "-C", str(ROOT), "rev-parse", "HEAD") != args.expected_source_sha:
            raise RuntimeError("source mismatch")
        if output("git", "-C", str(ROOT), "status", "--porcelain"):
            raise RuntimeError("dirty source")
        for tag, digest in ((REDIS_IMAGE, args.redis_image_id),
                            (CLIENT_IMAGE, args.client_image_id)):
            if output("docker", "image", "inspect", tag,
                      "--format", "{{.Id}}") != digest:
                raise RuntimeError("image changed")
        if (not SOURCE.is_file() or not (ROOT / "src/assistx").is_dir()
                or not absent("network", NETWORK)
                or not absent("container", REDIS)
                or not absent("container", CLIENT)):
            raise RuntimeError("source or disposable names unavailable")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        print("HOLD: unsafe initial source or topology")
        return 2

    network_id: str | None = None
    containers: dict[str, str] = {}
    stage = "network"
    try:
        network_id = output("docker", "network", "create", "--internal", NETWORK)
        if not CID.fullmatch(network_id):
            raise RuntimeError("bad isolated network identity")
        network_exact(network_id, set())

        stage = "redis"
        containers[REDIS] = output(
            "docker", "run", "-d", "--pull", "never", "--name", REDIS,
            "--network", NETWORK, "--read-only",
            "--tmpfs", "/data:rw,size=16m,mode=1777",
            "--tmpfs", "/tmp:rw,size=8m,mode=1777",
            "--user", "999:999", "--cpus", "0.25", "--memory", "128m",
            "--pids-limit", "32", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            args.redis_image_id, "redis-server", "--save", "", "--appendonly", "no",
        )
        redis_id = containers[REDIS]
        if not CID.fullmatch(redis_id):
            raise RuntimeError("bad disposable Redis identity")
        container_exact(redis_id, REDIS, args.redis_image_id)
        network_exact(network_id, {REDIS})
        ready = False
        for _ in range(30):
            p = run("docker", "exec", redis_id, "redis-cli", "PING", timeout=5)
            if p.returncode == 0 and p.stdout.strip() == b"PONG":
                ready = True
                break
            time.sleep(0.2)
        if not ready:
            raise RuntimeError("disposable Redis not ready")

        stage = "contention"
        containers[CLIENT] = output(
            "docker", "run", "-d", "--pull", "never", "--name", CLIENT,
            "--network", NETWORK, "--read-only", "--workdir", "/tmp",
            "--tmpfs", "/tmp:rw,size=16m,mode=1777",
            "--user", "1000:1000", "--cpus", "0.6", "--memory", "384m",
            "--pids-limit", "64", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--mount", f"type=bind,source={ROOT / 'src'},target=/work/src,readonly",
            "--mount", f"type=bind,source={SOURCE},target=/work/canary.py,readonly",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "ASSISTX_DISPOSABLE_REDIS_CONTENTION=synthetic-explicit-opt-in",
            "-e", f"ASSISTX_TEST_REDIS_HOST={REDIS}",
            "--entrypoint", "python", args.client_image_id, "/work/canary.py",
        )
        client_id = containers[CLIENT]
        if not CID.fullmatch(client_id):
            raise RuntimeError("bad disposable client identity")
        container_exact(client_id, CLIENT, args.client_image_id)
        network_exact(network_id, {REDIS, CLIENT})
        wait = run("docker", "wait", client_id, timeout=40)
        log = output("docker", "logs", client_id)
        if (wait.returncode or wait.stdout.strip() != b"0"
                or "REAL_REDIS_1_3_5_10_CONCURRENCY_FENCE_PASS" not in log):
            raise RuntimeError("physical contention proof missing")
        for size in (1, 3, 5, 10):
            if f"SYNTHETIC_WORKERS {size} ADMITTED {min(3,size)}" not in log:
                raise RuntimeError("missing physical concurrency band")
        print("REAL_REDIS_1_3_5_10_CONCURRENCY_FENCE_PASS")
        print("SYNTHETIC_CONCURRENT_REQUESTS=1,3,5,10")
        print("CAPACITY=3; ALL_LEASES_RELEASED")
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("DISPOSABLE_CONTENTION_FAIL_OR_INCONCLUSIVE", stage,
              type(exc).__name__)
        return 1
    finally:
        for name in (CLIENT, REDIS):
            identity = containers.get(name)
            if identity and CID.fullmatch(identity):
                run("docker", "stop", "-t", "1", identity, timeout=8)
                run("docker", "rm", "-f", identity, timeout=8)
        if network_id and CID.fullmatch(network_id):
            run("docker", "network", "rm", network_id, timeout=10)


if __name__ == "__main__":
    sys.exit(main())
