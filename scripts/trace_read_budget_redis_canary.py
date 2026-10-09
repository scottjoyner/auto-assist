#!/usr/bin/env python3
"""Operator-opt-in, disposable Redis Lua acceptance (NO production networks).

Never runs from pytest or CI by default. Requires an explicit approval flag and
exact locally cached Redis image ID. No bind mounts, ports or pulled images.
"""
from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

IMAGE = "redis:7-alpine"
ROOT = Path(__file__).resolve().parents[1]
LUA_PATH = ROOT / "src/assistx/trace_read_budget.py"
IMAGE_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def extract_script() -> str:
    module = ast.parse(LUA_PATH.read_text(encoding="utf-8"))
    scripts = [node.value.value for node in module.body
               if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "_ATOMIC_TRACE_BUDGET_LUA"
                       for t in node.targets)
               and isinstance(node.value, ast.Constant)
               and isinstance(node.value.value, str)]
    if len(scripts) != 1 or "QUOTA_LUA_SOURCE" in scripts[0]:
        raise RuntimeError("unexpected or ambiguous quota script")
    return scripts[0]


def validate_preflight(approved: bool, expected_image_id: str | None,
                       actual_image_id: str | None) -> None:
    if not approved:
        raise RuntimeError("explicit --allow-isolated-docker-test is required")
    if not isinstance(expected_image_id, str) or not IMAGE_PATTERN.fullmatch(expected_image_id):
        raise RuntimeError("a complete --expected-image-id sha256:<64 hex> is required")
    if actual_image_id != expected_image_id:
        raise RuntimeError("cached Redis image ID does not match the approved image")


def docker_command(name: str) -> list[str]:
    return [
        "docker", "run", "--rm", "--pull", "never", "--network", "none",
        "--ipc", "none", "--cpus", "0.25", "--memory", "96m",
        "--pids-limit", "64", "--read-only", "--tmpfs",
        "/tmp:rw,nosuid,noexec,size=16m", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "1000:1000",
        "--name", name, "--entrypoint", "/bin/sh", "-i", IMAGE, "-s",
    ]


def test_shell(lua: str) -> str:
    return '''set -eu
redis-server --daemonize yes --port 0 --unixsocket /tmp/redis.sock --unixsocketperm 700 --save "" --appendonly no --dir /tmp --maxmemory 32mb --maxclients 80 --logfile /tmp/redis.log
trap 'redis-cli -s /tmp/redis.sock shutdown nosave >/dev/null 2>&1 || true' EXIT
tries=0
until redis-cli -s /tmp/redis.sock ping 2>/dev/null | grep -q PONG; do tries=$((tries+1)); test "$tries" -lt 30; sleep 0.1; done
LUA=$(cat <<'QUOTA_LUA_SOURCE'
''' + lua + '''
QUOTA_LUA_SOURCE
)
request() { redis-cli -s /tmp/redis.sock --raw eval "$LUA" 1 "$1" "$2" "$3" "$4"; }
check_first() { test "$(printf '%s\\n' "$1" | head -1)" = "$2"; }
for i in $(seq 1 10); do response=$(request synthetic:sequential 60000 10 "seq-$i"); check_first "$response" 1; done
response=$(request synthetic:sequential 60000 10 eleventh); check_first "$response" 0
printf 'SEQUENTIAL_ALLOWED=10 DENIED_11TH=1 RETRY_SECONDS=%s\\n' "$(printf '%s\\n' "$response" | tail -1)"
response=$(request synthetic:other-identity 60000 10 fresh); check_first "$response" 1
printf 'IDENTITY_ISOLATION=PASS\\n'
for i in $(seq 1 24); do request synthetic:parallel 10000 10 "parallel-$i" > "/tmp/response-$i" & done
wait
allowed=0; rejected=0
for i in $(seq 1 24); do case "$(head -1 "/tmp/response-$i")" in 1) allowed=$((allowed+1));; 0) rejected=$((rejected+1));; *) echo INVALID_RESPONSE; exit 51;; esac; done
test "$allowed" -eq 10; test "$rejected" -eq 14
printf 'CONCURRENT_ALLOWED=%s CONCURRENT_DENIED=%s\\n' "$allowed" "$rejected"
response=$(request synthetic:reset 1100 1 first); check_first "$response" 1
response=$(request synthetic:reset 1100 1 second); check_first "$response" 0
sleep 1.3
response=$(request synthetic:reset 1100 1 third); check_first "$response" 1
printf 'SLIDING_WINDOW_RESET=PASS\\n'
printf 'REDIS_LUA_CANARY_PASS\\n'
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-isolated-docker-test", action="store_true")
    parser.add_argument("--expected-image-id", default=None)
    args = parser.parse_args(argv)
    if not args.allow_isolated_docker_test or not args.expected_image_id:
        print("BLOCKED: explicit approval and a pinned image ID are required", file=sys.stderr)
        return 2
    try:
        # Refuse to pull any image. Only examine the already cached image ID.
        cached = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
                                capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        validate_preflight(args.allow_isolated_docker_test, args.expected_image_id, cached)
        lua = extract_script()
        name = "assistx-rc1-redis-canary-" + uuid.uuid4().hex[:12]
        try:
            run = subprocess.run(docker_command(name), input=test_shell(lua),
                                 capture_output=True, text=True, timeout=70)
            print(run.stdout.strip())
            if run.stderr:
                print("Synthetic Redis error (redacted to last 500 bytes):", run.stderr[-500:], file=sys.stderr)
            if run.returncode != 0 or "REDIS_LUA_CANARY_PASS" not in run.stdout:
                return 1
            return 0
        finally:
            # Extra guard if the Docker client times out or the shell aborts.
            subprocess.run(["docker", "rm", "-f", name],
                           capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print("BLOCKED / FAILED: " + str(exc)[:180], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
