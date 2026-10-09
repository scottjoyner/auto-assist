#!/usr/bin/env python3
"""Explicit isolated Docker/real Redis restart negative control. NEVER production.

Runs no API, Neo4j, provider, graph queries, hosted inference or credentials.
Requires exact source HEAD and immutable local Docker image ID. All Redis
operations are scoped to one disposable no-network, no-persistent-data container.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import threading
import time

from assistx.trace_fenced_staging_adapter import (
    FencedReadUnavailable, StagingParameters, run_staging_fenced_read,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
NAME = "assistx-redis-boot-fence-disposable-20261009"
IMAGE = "redis:7-alpine"


def cmd(*parts: str, timeout: int = 12) -> str:
    result = subprocess.run(parts, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("isolated Docker operation failed: " + str(parts[1]))
    return result.stdout.decode("utf-8", errors="replace").strip()


def verify_container(expected_image_id: str) -> None:
    obj = json.loads(cmd("docker", "inspect", "--type", "container", NAME))[0]
    host = obj.get("HostConfig") or {}
    if (
        obj.get("Name") != "/" + NAME
        or obj.get("Image") != expected_image_id
        or not obj.get("State", {}).get("Running")
        or host.get("NetworkMode") != "none"
        or bool(host.get("PortBindings"))
        or not host.get("ReadonlyRootfs")
        or host.get("Memory", 0) > 128 * 1024**2
        or host.get("NanoCpus", 0) > 250_000_000
        or host.get("Privileged")
        or bool(host.get("Binds"))
        or bool(obj.get("Mounts"))
    ):
        raise RuntimeError("disposable Redis topology not attested")


class DockerRedis:
    def cli(self, *args: str) -> str:
        return cmd("docker", "exec", NAME, "redis-cli", "--raw", *args)

    def info(self, section: str = "server") -> dict[str, str]:
        if section != "server":
            raise ValueError("only server identity is permitted")
        return {
            key: value.strip()
            for line in self.cli("INFO", "server").splitlines()
            if ":" in line and not line.startswith("#")
            for key, value in [line.split(":", 1)]
        }

    def eval(self, script: str, numkeys: int, *values: object):
        raw = self.cli("EVAL", script, str(numkeys), *(str(x) for x in values))
        items = raw.splitlines()
        if not items or any(not x.lstrip("-").isdigit() for x in items):
            raise RuntimeError("unexpected synthetic Redis Lua response")
        return int(items[0]) if len(items) == 1 else [int(x) for x in items]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-disposable-restart", action="store_true")
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-image-id", required=True)
    args = parser.parse_args(argv)
    if not args.approve_disposable_restart:
        print("HOLD: explicit disposable test approval missing")
        return 2
    if not args.expected_source_sha or len(args.expected_source_sha) != 40:
        print("HOLD: malformed source identity")
        return 2
    if not args.expected_image_id.startswith("sha256:") or len(args.expected_image_id) != 71:
        print("HOLD: immutable Docker identity required")
        return 2
    try:
        if cmd("git", "-C", str(ROOT), "rev-parse", "HEAD") != args.expected_source_sha:
            raise RuntimeError("source identity mismatch")
        if cmd("git", "-C", str(ROOT), "status", "--porcelain"):
            raise RuntimeError("dirty checkout")
        if cmd("docker", "image", "inspect", IMAGE, "--format", "{{.Id}}") != args.expected_image_id:
            raise RuntimeError("local immutable image mismatch")
        exists = subprocess.run(
            ["docker", "inspect", "--type", "container", NAME],
            capture_output=True, timeout=8,
        )
        if exists.returncode == 0:
            raise RuntimeError("existing named container must not be touched")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("HOLD:", type(exc).__name__)
        return 2

    started = False
    try:
        cmd(
            "docker", "run", "--rm", "-d", "--pull", "never",
            "--name", NAME, "--network", "none", "--read-only",
            "--tmpfs", "/data:rw,nosuid,size=16m,mode=1777",
            "--tmpfs", "/tmp:rw,nosuid,size=8m,mode=1777",
            "--user", "999:999", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--cpus", "0.25",
            "--memory", "128m", "--pids-limit", "32", IMAGE,
            "redis-server", "--save", "", "--appendonly", "no",
        )
        started = True
        verify_container(args.expected_image_id)
        redis = DockerRedis()
        for _ in range(30):
            try:
                if redis.cli("PING") == "PONG":
                    break
            except Exception:
                pass
            time.sleep(.15)
        else:
            raise RuntimeError("disposable Redis never became ready")
        pin = redis.info()["run_id"]
        if len(pin) != 40:
            raise RuntimeError("Redis process identity malformed")

        entered = threading.Event()
        cancelled = threading.Event()
        output: dict[str, object] = {}

        def query(stop: threading.Event) -> str:
            entered.set()
            stop.wait(5)
            return "SYNTHETIC_MUST_NOT_ESCAPE"

        def task() -> None:
            try:
                output["result"] = run_staging_fenced_read(
                    redis_client=redis, principal="synthetic-isolated-reader",
                    receiver_key="synthetic-only-receiver-owned-1234567890",
                    redis_run_id_pin=pin, query=query,
                    request_cancel=cancelled.set,
                    parameters=StagingParameters(
                        heartbeat_seconds=.12, watchdog_join_seconds=1.5
                    ),
                )
            except BaseException as exc:
                output["exception"] = type(exc).__name__

        worker = threading.Thread(target=task, daemon=True)
        worker.start()
        if not entered.wait(5):
            raise RuntimeError("synthetic read failed to start")
        before = redis.cli("EXISTS", "traceidx:{assistx-trace-index-v2}:active:fleet")
        if before != "1":
            raise RuntimeError("synthetic lease absent before Redis restart")
        verify_container(args.expected_image_id)
        cmd("docker", "restart", NAME, timeout=18)
        verify_container(args.expected_image_id)
        for _ in range(20):
            try:
                current = redis.info()["run_id"]
                if current != pin:
                    break
            except Exception:
                pass
            time.sleep(.15)
        else:
            raise RuntimeError("Redis process identity unchanged")
        worker.join(timeout=8)
        if worker.is_alive():
            raise RuntimeError("synthetic reader did not terminate")
        if output.get("exception") != FencedReadUnavailable.__name__:
            raise RuntimeError("failed to deny result after Redis process restart")
        if not cancelled.is_set():
            raise RuntimeError("no cancellation signal after Redis restart")
        if redis.cli("EXISTS", "traceidx:{assistx-trace-index-v2}:active:fleet") != "0":
            raise RuntimeError("disposable non-persistent Redis unexpectedly retained lease")
        print("REDIS_RESTART_FENCED_READ_DENIAL_PASS")
        print("old_read_result_returned=false")
        print("redis_boot_identity_changed=true")
        print("synthetic_lease_state_lost=true")
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("ISOLATED_RESTART_PROBE_FAIL_OR_INCONCLUSIVE")
        return 1
    finally:
        if started:
            subprocess.run(
                ["docker", "stop", "-t", "2", NAME],
                capture_output=True, timeout=10, check=False,
            )


if __name__ == "__main__":
    sys.exit(main())
