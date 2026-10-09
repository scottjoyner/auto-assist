#!/usr/bin/env python3
"""Explicit-only, network-none real Redis canary for source-owned Lua fences.

Requires operator opt-in and exact locally cached image SHA256. Never runs
from general pytest or on live Redis. No network, host port or bind mounts.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/assistx/trace_index_fenced_research.py"
IMAGE = "redis:7-alpine"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SCRIPTS = ("ACQUIRE_LUA", "RELEASE_LUA", "RENEW_LUA")


def scripts_from_source() -> dict[str, str]:
    tree = ast.parse(SOURCE.read_text(encoding="utf8"))
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in SCRIPTS:
                    if not isinstance(node.value.value, str):
                        raise ValueError("missing literal Redis script")
                    result[target.id] = node.value.value
    if set(result) != set(SCRIPTS) or any("REDIS_LUA_HEREDOC" in x for x in result.values()):
        raise ValueError("expected exactly three source-owned Redis scripts")
    return result


def command(image_digest: str, name: str) -> list[str]:
    if not DIGEST.fullmatch(image_digest):
        raise ValueError("must launch immutable approved image, not mutable tag")
    if not re.fullmatch(r"assistx-fence-study-[0-9a-f]{12}", name):
        raise ValueError("invalid disposable name")
    return ["docker", "run", "--rm", "--pull", "never", "--network", "none",
            "--ipc", "none", "--cpus", "0.25", "--memory", "96m",
            "--pids-limit", "64", "--read-only", "--tmpfs",
            "/tmp:rw,nosuid,noexec,size=16m", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "1000:1000",
            "--name", name, "--entrypoint", "/bin/sh", "-i", image_digest, "-s"]


def shell_code(scripts: dict[str, str]) -> str:
    blocks = []
    for name in SCRIPTS:
        blocks.append(name + "=$(cat <<'REDIS_LUA_HEREDOC'\n" + scripts[name] + "\nREDIS_LUA_HEREDOC\n)")
    return """set -eu
redis-server --daemonize yes --port 0 --unixsocket /tmp/redis.sock --unixsocketperm 700 --save "" --appendonly no --dir /tmp --maxmemory 32mb --maxclients 80 --logfile /tmp/redis.log
trap 'redis-cli -s /tmp/redis.sock shutdown nosave >/dev/null 2>&1 || true' EXIT
tries=0
until redis-cli -s /tmp/redis.sock ping 2>/dev/null | grep -q PONG; do tries=$((tries+1)); test "$tries" -lt 35; sleep .1; done
""" + "\n".join(blocks) + """
root='traceidx:{assistx-trace-index-v2}:'
acq() {
  user=$1; namespace=$2; pm=$3; gm=$4; win=$5; ps=$6; gs=$7; lease=$8; nonce=$9
  redis-cli -s /tmp/redis.sock --raw eval "$ACQUIRE_LUA" 4 \\
    "${root}${namespace}:rate:principal:${user}" "${root}${namespace}:rate:fleet" \\
    "${root}${namespace}:active:principal:${user}" "${root}${namespace}:active:fleet" \\
    "$pm" "$gm" "$win" "$ps" "$gs" "$lease" "$nonce"
}
rel() {
  redis-cli -s /tmp/redis.sock --raw eval "$RELEASE_LUA" 2 \\
    "${root}${2}:active:principal:${1}" "${root}${2}:active:fleet" "$3"
}
renew() {
  redis-cli -s /tmp/redis.sock --raw eval "$RENEW_LUA" 2 \\
    "${root}${2}:active:principal:${1}" "${root}${2}:active:fleet" "$3" "$4"
}
first() { printf '%s\\n' "$1" | head -1; }
second() { printf '%s\\n' "$1" | sed -n '2p'; }
assert_code() { test "$(first "$1")" = "$2" && test "$(second "$1")" = "$3"; }
# Rate windows and separate identities: exactly two allowed fleet-wide.
a=$(acq fixture-a rate 3 2 60000 3 3 15000 00000000000000000000000000000001)
b=$(acq fixture-b rate 3 2 60000 3 3 15000 00000000000000000000000000000002)
c=$(acq fixture-c rate 3 2 60000 3 3 15000 00000000000000000000000000000003)
assert_code "$a" 1 0; assert_code "$b" 1 0; assert_code "$c" 0 2
printf 'FLEET_RATE=2_ALLOWED_1_DENIED\\n'
# Burst: 16 concurrent attempts, global in-flight capacity=3.
for i in $(seq 1 16); do
 nonce=$(printf '%032x' "$((100+i))")
 acq fixture burst 32 32 60000 16 3 15000 "$nonce" > "/tmp/fence-$i" &
done
wait
allowed=0; rejected=0
for i in $(seq 1 16); do
 code=$(head -1 "/tmp/fence-$i")
 case "$code" in 1) allowed=$((allowed+1));; 0) rejected=$((rejected+1));; *) echo BAD_REDIS_RESPONSE; exit 31;; esac
done
test "$allowed" -eq 3 && test "$rejected" -eq 13
printf 'PARALLEL_ALLOWED=%s PARALLEL_DENIED=%s\\n' "$allowed" "$rejected"
# Per-principal in-flight cap is stricter than fleet cap.
d=$(acq fixture personal 10 10 60000 1 3 15000 00000000000000000000000000000021)
e=$(acq fixture personal 10 10 60000 1 3 15000 00000000000000000000000000000022)
assert_code "$d" 1 0; assert_code "$e" 0 3
printf 'PRINCIPAL_INFLIGHT=PASS\\n'
# Denial does not consume rate; after releasing, a new admission succeeds.
out=$(rel fixture personal 00000000000000000000000000000021)
test "$out" -eq 1
out=$(rel fixture personal 00000000000000000000000000000021)
test "$out" -eq 0
f=$(acq fixture personal 10 10 60000 1 3 15000 00000000000000000000000000000023)
assert_code "$f" 1 0
printf 'RELEASE_NO_DOUBLE_FREE=PASS\\n'
# Replay nonce rejected without consuming admission slot.
q=$(acq fixture personal 10 10 60000 1 3 15000 00000000000000000000000000000023)
assert_code "$q" 0 5
printf 'NONCE_REPLAY=PASS\\n'
# A second user's stale nonce cannot remove a valid active slot.
out=$(rel stranger personal 00000000000000000000000000000023)
test "$out" -eq 0
printf 'WRONG_OWNER_RELEASE=DENIED\\n'
# Renewal must preserve the same nonce and expire if not renewed in time.
g=$(acq fixture lease 10 10 60000 1 1 1200 00000000000000000000000000000031)
assert_code "$g" 1 0
sleep .5
out=$(renew fixture lease 00000000000000000000000000000031 1200)
test "$out" -eq 1
sleep .85
h=$(acq fixture lease 10 10 60000 1 1 1200 00000000000000000000000000000032)
assert_code "$h" 0 3
out=$(rel fixture lease 00000000000000000000000000000031)
test "$out" -eq 1
h=$(acq fixture lease 10 10 60000 1 1 1200 00000000000000000000000000000033)
assert_code "$h" 1 0
printf 'RENEW_LEASE_STAYS_FENCED=PASS\\n'
# New lease after expiry cannot be freed by a late old nonce.
i=$(acq fixture expiry 10 10 60000 1 1 1100 00000000000000000000000000000041)
assert_code "$i" 1 0
sleep 1.3
j=$(acq fixture expiry 10 10 60000 1 1 1100 00000000000000000000000000000042)
assert_code "$j" 1 0
out=$(rel fixture expiry 00000000000000000000000000000041)
test "$out" -eq 0
k=$(acq fixture expiry 10 10 60000 1 1 1100 00000000000000000000000000000043)
assert_code "$k" 0 3
printf 'STALE_RELEASE_CANNOT_FREE_NEW_LEASE=PASS\\n'
echo UNIFIED_FENCED_REDIS_CANARY_PASS
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-isolated-docker-test", action="store_true")
    parser.add_argument("--expected-image-id")
    options = parser.parse_args(argv)
    if not options.allow_isolated_docker_test or not options.expected_image_id:
        print("BLOCKED: explicit approval and pinned image are required", file=sys.stderr)
        return 2
    if not DIGEST.fullmatch(options.expected_image_id):
        print("BLOCKED: immutable image ID is required", file=sys.stderr)
        return 2
    name = "assistx-fence-study-" + uuid.uuid4().hex[:12]
    try:
        cached = subprocess.run(["docker", "image", "inspect", options.expected_image_id,
                                 "--format", "{{.Id}}"], capture_output=True,
                                text=True, check=True, timeout=10).stdout.strip()
        if cached != options.expected_image_id:
            raise RuntimeError("cached Redis image is not the approved image")
        proc = subprocess.run(command(cached, name), input=shell_code(scripts_from_source()),
                              capture_output=True, text=True, timeout=65)
        if proc.returncode != 0 or "UNIFIED_FENCED_REDIS_CANARY_PASS" not in proc.stdout:
            print("FAILED canary exit=" + str(proc.returncode), file=sys.stderr)
            print(proc.stdout[-1800:], file=sys.stderr)
            print(proc.stderr[-800:], file=sys.stderr)
            return 1
        print(proc.stdout.strip())
        return 0
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        print("BLOCKED/FAILED: " + str(exc)[:160], file=sys.stderr)
        return 2
    finally:
        # Never touch another container: name is randomly generated here.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                       text=True, timeout=10, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
