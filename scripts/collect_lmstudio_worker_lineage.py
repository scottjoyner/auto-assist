#!/usr/bin/env python3
"""Observe local LM Studio -> llama-server parent/child listener ownership.

No credentials, inference, model loading, signing, Neo4j or remote connections.
A process lineage and socket relationship is NOT request execution attestation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

_MAX_PID = 2**31 - 1
_MAX_PORT = 65535


def _positive(value: int, label: str, limit: int) -> int:
    if type(value) is not int or not 1 <= value <= limit:
        raise ValueError("invalid " + label)
    return value


def proc_identity(proc_root: Path, pid: int) -> tuple[str, int, int]:
    """Parse /proc/PID/stat fields 3/4/22, even with parentheses in comm."""
    root = proc_root / str(pid)
    raw = (root / "stat").read_text()
    end = raw.rfind(") ")
    if end < 2:
        raise ValueError("malformed Linux stat")
    fields = raw[end + 2:].split()
    if len(fields) < 20:
        raise ValueError("incomplete Linux stat")
    parent = int(fields[1])
    ticks = int(fields[19])
    comm = (root / "comm").read_text().strip()
    if parent < 1 or ticks <= 0 or not comm:
        raise ValueError("invalid Linux process identity")
    return comm, parent, ticks


def socket_inodes(proc_root: Path, pid: int) -> set[str]:
    result = set()
    for link in (proc_root / str(pid) / "fd").iterdir():
        try:
            target = os.readlink(link)
        except (FileNotFoundError, PermissionError, OSError):
            continue
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        if match:
            result.add(match[1])
    if not result:
        raise ValueError("process has no inspectable socket descriptors")
    return result


def listen_inodes(table: Path, port: int) -> set[str]:
    """Read Linux /proc/net/tcp{,6} exclusively for LISTEN state (0A)."""
    matching = set()
    try:
        lines = table.read_text().splitlines()[1:]
    except FileNotFoundError:
        return matching
    for row in lines:
        fields = row.split()
        if len(fields) < 10 or fields[3] != "0A":
            continue
        try:
            found = int(fields[1].split(":")[-1], 16)
        except ValueError:
            continue
        if found == port and fields[9].isdigit():
            matching.add(fields[9])
    return matching


def listener_owner(
    *, proc_root: Path, tcp_tables: tuple[Path, Path],
    pid: int, port: int,
) -> bool:
    listening = set().union(*(listen_inodes(table, port) for table in tcp_tables))
    return bool(listening & socket_inodes(proc_root, pid))


def get_worker_health(port: int) -> bool:
    # Guaranteed only local HTTP, no proxy, no redirect, no authentication.
    class RefuseRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), RefuseRedirect()
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health", method="GET",
        headers={"Accept": "application/json"},
    )
    with opener.open(request, timeout=3) as result:
        if result.status != 200:
            return False
        raw = result.read(2049)
    if len(raw) > 2048:
        return False
    data = json.loads(raw)
    return isinstance(data, dict) and data.get("status") == "ok"


def observe(
    *, gateway_pid: int, worker_pid: int, gateway_port: int,
    worker_port: int, proc_root: Path = Path("/proc"),
    tcp_tables: tuple[Path, Path] = (
        Path("/proc/net/tcp"), Path("/proc/net/tcp6"),
    ),
    boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id"),
    health_reader=get_worker_health,
) -> dict:
    _positive(gateway_pid, "gateway_pid", _MAX_PID)
    _positive(worker_pid, "worker_pid", _MAX_PID)
    _positive(gateway_port, "gateway_port", _MAX_PORT)
    _positive(worker_port, "worker_port", _MAX_PORT)
    if gateway_pid == worker_pid or gateway_port == worker_port:
        raise ValueError("gateway and model worker must be distinct")

    boot_before = str(uuid.UUID(boot_id_path.read_text().strip()))
    gateway = proc_identity(proc_root, gateway_pid)
    worker = proc_identity(proc_root, worker_pid)
    if gateway[0] != "lm-studio" or worker[0] != "llama-server":
        raise ValueError("unexpected gateway/worker executable identity")
    if worker[1] != gateway_pid:
        raise ValueError("model worker is not an LM Studio child")
    if not listener_owner(
        proc_root=proc_root, tcp_tables=tcp_tables,
        pid=gateway_pid, port=gateway_port,
    ):
        raise ValueError("LM Studio does not own approved API listener")
    if not listener_owner(
        proc_root=proc_root, tcp_tables=tcp_tables,
        pid=worker_pid, port=worker_port,
    ):
        raise ValueError("expected model worker does not own direct listener")

    health = False
    try:
        health = health_reader(worker_port) is True
    except (urllib.error.URLError, OSError, ValueError, TimeoutError,
            json.JSONDecodeError):
        health = False

    # No model completion; ensure the exact PID/boot/inode evidence still holds.
    if (str(uuid.UUID(boot_id_path.read_text().strip())) != boot_before
            or gateway != proc_identity(proc_root, gateway_pid)
            or worker != proc_identity(proc_root, worker_pid)
            or not listener_owner(
                proc_root=proc_root, tcp_tables=tcp_tables,
                pid=gateway_pid, port=gateway_port,
            )
            or not listener_owner(
                proc_root=proc_root, tcp_tables=tcp_tables,
                pid=worker_pid, port=worker_port,
            )):
        raise ValueError("process or listening socket changed during observation")
    return {
        "schema": "lmstudio-worker-lineage-observation-v1",
        "source_kind": "unsigned_host_local_linux_process",
        "observed_at_ms": int(time.time() * 1000),
        "boot_id": boot_before,
        "gateway_pid": gateway_pid,
        "gateway_process_start_ticks": gateway[2],
        "gateway_listener_port": gateway_port,
        "worker_pid": worker_pid,
        "worker_process_start_ticks": worker[2],
        "worker_listener_port": worker_port,
        "worker_parent_pid": worker[1],
        "listener_ownership_observed": True,
        "direct_worker_health_ok": health,
        "physical_model_digest_checked_by_this_tool": False,
        "model_loaded_inference_attested": False,
        "gateway_forwarding_attested": False,
        "source_signature_verified": False,
        "operator_approval_present": False,
        "owner_bound_request_attested": False,
        "can_approve": False,
        "can_dispatch": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-pid", type=int, required=True)
    parser.add_argument("--worker-pid", type=int, required=True)
    parser.add_argument("--gateway-port", type=int, required=True)
    parser.add_argument("--worker-port", type=int, required=True)
    args = parser.parse_args()
    try:
        observation = observe(
            gateway_pid=args.gateway_pid,
            worker_pid=args.worker_pid,
            gateway_port=args.gateway_port,
            worker_port=args.worker_port,
        )
    except (ValueError, OSError, UnicodeError) as exc:
        print("host lineage refused: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(observation, sort_keys=True))
    return 0 if observation["direct_worker_health_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
