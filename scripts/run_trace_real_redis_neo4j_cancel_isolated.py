#!/usr/bin/env python3
"""Owner-approved, disposable Redis-loss + REAL Neo4j cancellation canary.

Requires exact committed source and immutable *local* Docker image IDs.
Only creates its own named internal Docker network, two temporary databases,
and a read-only client. Never contacts or modifies production.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
NETWORK = "assistx-neo-redis-cancel-internal-20261009"
NEO = "assistx-neo-redis-fence-isolated-20261009"
REDIS = "assistx-redis-neo-fence-isolated-20261009"
CLIENT = "assistx-redis-neo-fence-client-20261009"
CLIENT_SCRIPT = ROOT / "scripts/trace_real_redis_neo4j_cancel_client.py"
IMAGES = {"neo": "neo4j:5.26-enterprise", "redis": "redis:7-alpine",
          "client": "git-assistx:latest"}
HEX = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
CID = re.compile(r"^[0-9a-f]{64}$")


def cmd(*args: str, timeout: float = 20) -> str:
    p = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError("disposable Docker/Git operation rejected")
    return p.stdout.decode("utf-8", "replace").strip()


def absent(category: str, name: str) -> bool:
    p = subprocess.run(
        ["docker", category, "inspect", name],
        capture_output=True, timeout=8, check=False,
    )
    return p.returncode != 0


def container_exact(cid: str, name: str, image: str, network: str) -> dict:
    info = json.loads(cmd("docker", "inspect", "--type", "container", cid))[0]
    host = info.get("HostConfig") or {}
    if (info.get("Id") != cid or info.get("Name") != "/" + name
            or info.get("Image") != image or not info.get("State", {}).get("Running")
            or host.get("NetworkMode") != network
            or bool(host.get("PortBindings")) or bool(host.get("Binds"))
            or bool(info.get("Mounts"))):
        raise RuntimeError("disposable container identity/fences failed")
    return info


def network_exact(network_id: str, names: set[str]) -> None:
    net = json.loads(cmd("docker", "network", "inspect", network_id))[0]
    if (net.get("Id") != network_id or net.get("Name") != NETWORK
            or net.get("Internal") is not True
            or net.get("Attachable") is True or net.get("Ingress") is True):
        raise RuntimeError("noninternal or unapproved network")
    peers = {x.get("Name") for x in (net.get("Containers") or {}).values()}
    if peers != names:
        raise RuntimeError("unexpected network peer; stop experiment")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--approve-disposable-combined-probe", action="store_true")
    ap.add_argument("--expected-source-sha", required=True)
    for role in IMAGES:
        ap.add_argument("--" + role + "-image-id", required=True)
    args = ap.parse_args(argv)
    ids = {role: getattr(args, role + "_image_id") for role in IMAGES}
    if (not args.approve_disposable_combined_probe
            or not isinstance(args.expected_source_sha, str)
            or not HEX.fullmatch(args.expected_source_sha)
            or not all(isinstance(x, str) and DIGEST.fullmatch(x) for x in ids.values())):
        print("HOLD: approved exact source/image identities required")
        return 2

    try:
        if cmd("git", "-C", str(ROOT), "rev-parse", "HEAD") != args.expected_source_sha:
            raise RuntimeError("source revision changed")
        if cmd("git", "-C", str(ROOT), "status", "--porcelain"):
            raise RuntimeError("dirty or unreviewed source")
        if not CLIENT_SCRIPT.is_file():
            raise RuntimeError("missing pinned client source")
        for role, image in IMAGES.items():
            if cmd("docker", "image", "inspect", image,
                   "--format", "{{.Id}}") != ids[role]:
                raise RuntimeError("immutable image mismatch")
        if not absent("network", NETWORK):
            raise RuntimeError("preexisting network; refuse reuse")
        if any(not absent("container", name) for name in (NEO, REDIS, CLIENT)):
            raise RuntimeError("preexisting named container; refuse reuse")
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        print("HOLD: source or disposable topology preflight failed")
        return 2

    network_id = None
    owned: dict[str, str] = {}
    try:
        network_id = cmd("docker", "network", "create", "--internal", NETWORK)
        if not CID.fullmatch(network_id):
            raise RuntimeError("invalid network ID")
        network_exact(network_id, set())

        owned[REDIS] = cmd(
            "docker", "run", "--rm", "-d", "--pull", "never", "--name", REDIS,
            "--network", NETWORK, "--read-only",
            "--tmpfs", "/data:rw,size=16m,mode=1777",
            "--tmpfs", "/tmp:rw,size=8m,mode=1777",
            "--user", "999:999", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--cpus", "0.25",
            "--memory", "128m", "--pids-limit", "32",
            IMAGES["redis"], "redis-server", "--save", "", "--appendonly", "no",
        )
        if not CID.fullmatch(owned[REDIS]):
            raise RuntimeError("unidentified disposable Redis")

        owned[NEO] = cmd(
            "docker", "run", "--rm", "-d", "--pull", "never", "--name", NEO,
            "--network", NETWORK, "--cpus", "0.75", "--memory", "2g",
            "--pids-limit", "160", "--security-opt", "no-new-privileges",
            "--tmpfs", "/data:rw,size=640m,mode=1777",
            "--tmpfs", "/logs:rw,size=32m,mode=1777",
            "--tmpfs", "/tmp:rw,size=64m,mode=1777",
            "-e", "NEO4J_AUTH=none",
            "-e", "NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
            "-e", "NEO4J_server_memory_heap_initial__size=256m",
            "-e", "NEO4J_server_memory_heap_max__size=512m",
            IMAGES["neo"],
        )
        if not CID.fullmatch(owned[NEO]):
            raise RuntimeError("unidentified disposable Neo4j")
        container_exact(owned[REDIS], REDIS, ids["redis"], NETWORK)
        container_exact(owned[NEO], NEO, ids["neo"], NETWORK)
        network_exact(network_id, {REDIS, NEO})

        # Wait for the *disposable* scratch server's offline/online admin.
        ready = False
        for _ in range(75):
            p = subprocess.run(
                ["docker", "exec", owned[NEO], "cypher-shell", "-d", "system",
                 "SHOW DATABASES"],
                capture_output=True, timeout=6, check=False,
            )
            if p.returncode == 0:
                ready = True
                break
            time.sleep(.5)
        if not ready:
            raise RuntimeError("disposable Neo4j scratch system not ready")
        # This is confined to the new tmpfs-backed synthetic database only.
        for statement in (
            "CALL dbms.setConfigValue('server.databases.read_only', '')",
            "START DATABASE neo4j WAIT",
        ):
            p = subprocess.run(
                ["docker", "exec", owned[NEO], "cypher-shell", "-d", "system",
                 statement],
                capture_output=True, timeout=22, check=False,
            )
            if p.returncode:
                raise RuntimeError("synthetic Neo4j scratch database not online")

        owned[CLIENT] = cmd(
            "docker", "run", "--rm", "-d", "--pull", "never", "--name", CLIENT,
            "--network", NETWORK, "--read-only",
            "--tmpfs", "/tmp:rw,size=16m,mode=1777",
            "--user", "1000:1000", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--cpus", "0.4",
            "--memory", "512m", "--pids-limit", "64",
            "--mount", f"type=bind,source={ROOT / 'src'},target=/work/src,readonly",
            "--mount", f"type=bind,source={CLIENT_SCRIPT},target=/work/canary.py,readonly",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "ASSISTX_DISPOSABLE_REDIS_NEO_CANARY=synthetic-explicit-opt-in",
            "-e", f"ASSISTX_TEST_TARGET_URI=bolt://{NEO}:7687",
            "--entrypoint", "python", IMAGES["client"], "/work/canary.py",
        )
        if not CID.fullmatch(owned[CLIENT]):
            raise RuntimeError("unidentified disposable client")
        network_exact(network_id, {REDIS, NEO, CLIENT})
        seen = False
        for _ in range(70):
            log = cmd("docker", "logs", owned[CLIENT], timeout=6)
            if "REAL_GRAPH_TRANSACTION_VISIBLE" in log:
                seen = True
                break
            status = json.loads(cmd("docker", "inspect", owned[CLIENT]))[0]["State"]
            if status.get("Running") is not True:
                break
            time.sleep(.1)
        if not seen:
            raise RuntimeError("real Neo4j transaction never observed")
        # Before restarting, reassert exact container+network custody.
        container_exact(owned[REDIS], REDIS, ids["redis"], NETWORK)
        network_exact(network_id, {REDIS, NEO, CLIENT})
        cmd("docker", "restart", "-t", "1", owned[REDIS], timeout=18)

        p = subprocess.run(
            ["docker", "wait", owned[CLIENT]],
            capture_output=True, text=True, timeout=18, check=False,
        )
        if p.returncode or p.stdout.strip() != "0":
            raise RuntimeError("cancellation client failed or timed out")
        log = cmd("docker", "logs", owned[CLIENT])
        if "REAL_REDIS_RESTART_NEO4J_CANCEL_SYNTHETIC_PASS" not in log:
            raise RuntimeError("no independent physical cancellation witness")
        print("REAL_REDIS_RESTART_NEO4J_CANCEL_SYNTHETIC_PASS")
        print("NO_PRODUCTION_NETWORK_PORTS_OR_CREDENTIALS_USED")
        return 0
    except (OSError, RuntimeError, ValueError, KeyError,
            subprocess.SubprocessError) as exc:
        print("DISPOSABLE_COMBINED_CANARY_FAIL_OR_INCONCLUSIVE")
        return 1
    finally:
        for name in (CLIENT, NEO, REDIS):
            ident = owned.get(name)
            if ident and CID.fullmatch(ident):
                subprocess.run(["docker", "stop", "-t", "2", ident],
                               capture_output=True, timeout=10, check=False)
        if network_id and CID.fullmatch(network_id):
            subprocess.run(["docker", "network", "rm", network_id],
                           capture_output=True, timeout=10, check=False)


if __name__ == "__main__":
    sys.exit(main())
