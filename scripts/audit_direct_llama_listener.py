#!/usr/bin/env python3
"""Read-only, fail-closed DIRECT llama-server host/listener preflight.

This is NOT a model-execution receipt or an authorization service.
No HTTP POST, model invocation, privileged files, credentials, process
signals, firewall modifications, new ports, Neo4j or network discovery.
It reads only local /proc and hashes a bounded local model file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import time
import uuid
from pathlib import Path
from typing import Any

MAX_MODEL_BYTES = 2 * 1024**3
MAX_CMDLINE_BYTES = 16_384
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _positive(value: Any, label: str, upper: int) -> int:
    if type(value) is not int or not 1 <= value <= upper:
        raise ValueError("invalid " + label)
    return value


def _flag_values(args: list[str], flags: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for i, arg in enumerate(args):
        for flag in flags:
            if arg == flag:
                if i + 1 >= len(args) or args[i + 1].startswith("-"):
                    raise ValueError("missing process flag argument")
                found.append(args[i + 1])
            elif arg.startswith(flag + "="):
                found.append(arg[len(flag) + 1:])
    return found


def _one_flag(args: list[str], flags: tuple[str, ...], label: str) -> str:
    found = _flag_values(args, flags)
    if len(found) != 1 or not found[0]:
        raise ValueError("missing or ambiguous " + label)
    return found[0]


def _process(proc_root: Path, pid: int) -> tuple[int, bytes, Path, str, bool]:
    root = proc_root / str(pid)
    raw = (root / "cmdline").read_bytes()
    if not 0 < len(raw) <= MAX_CMDLINE_BYTES:
        raise ValueError("unbounded or absent process argv")
    args = [x.decode("utf-8") for x in raw.split(b"\0") if x]
    if not args or Path(args[0]).name != "llama-server":
        raise ValueError("PID is not an independently managed llama-server")
    stat_line = (root / "stat").read_text()
    end = stat_line.rfind(") ")
    if end < 2:
        raise ValueError("invalid Linux /proc stat")
    fields = stat_line[end + 2:].split()
    if len(fields) < 20:
        raise ValueError("incomplete process stat")
    start_ticks = int(fields[19])
    if start_ticks <= 0:
        raise ValueError("invalid process start counter")
    model_path = Path(_one_flag(args, ("-m", "--model"), "model file"))
    declared_host = _one_flag(args, ("--host",), "host binding")
    explicit_key_flag = any(
        a == key or a.startswith(key + "=")
        for a in args
        for key in ("--api-key", "--api-key-file")
    )
    return start_ticks, raw, model_path, declared_host, explicit_key_flag


def _listeners(
    proc_root: Path, tcp_tables: tuple[Path, Path], pid: int, port: int,
) -> set[str]:
    file_descriptors: set[str] = set()
    for entry in (proc_root / str(pid) / "fd").iterdir():
        try:
            match = re.fullmatch(r"socket:\[(\d+)\]", os.readlink(entry))
            if match:
                file_descriptors.add(match[1])
        except (FileNotFoundError, PermissionError, OSError):
            continue
    if not file_descriptors:
        raise ValueError("process has no readable socket descriptors")

    bindings = set()
    for table in tcp_tables:
        if not table.is_file():
            continue
        for row in table.read_text().splitlines()[1:]:
            fields = row.split()
            if len(fields) < 10 or fields[3] != "0A":
                continue
            address, hex_port = fields[1].rsplit(":", 1)
            if int(hex_port, 16) != port or fields[9] not in file_descriptors:
                continue
            if address in ("0100007F", "00000000000000000000000001000000"):
                bindings.add("loopback")
            elif address in ("00000000", "0" * 32):
                bindings.add("wildcard")
            else:
                bindings.add("non_loopback")
    if not bindings:
        raise ValueError("expected port not owned by process LISTEN socket")
    return bindings


def collect(
    *, pid: int, port: int, expected_sha256: str | None = None,
    proc_root: Path = Path("/proc"),
    boot_file: Path = Path("/proc/sys/kernel/random/boot_id"),
    tcp_tables: tuple[Path, Path] = (
        Path("/proc/net/tcp"), Path("/proc/net/tcp6"),
    ),
    max_model_bytes: int = MAX_MODEL_BYTES,
) -> dict[str, Any]:
    _positive(pid, "PID", 2**31 - 1)
    _positive(port, "port", 65535)
    _positive(max_model_bytes, "model byte budget", MAX_MODEL_BYTES)
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or DIGEST.fullmatch(expected_sha256) is None
    ):
        raise ValueError("expected SHA256 must be independently pinned lowercase hex")

    boot_before = str(uuid.UUID(boot_file.read_text().strip()))
    start, argv_before, model_path, host, key_flag = _process(proc_root, pid)
    if str(port) != _one_flag(
        [x.decode() for x in argv_before.split(b"\0") if x],
        ("--port",), "port binding",
    ):
        raise ValueError("requested port differs from process command line")
    if not model_path.is_absolute() or model_path.is_symlink():
        raise ValueError("model must have an absolute, nonsymlink file path")
    initial = model_path.stat()
    if not stat.S_ISREG(initial.st_mode) or not 0 < initial.st_size <= max_model_bytes:
        raise ValueError("model file type or size not within resource budget")
    socket_before = _listeners(proc_root, tcp_tables, pid, port)

    with model_path.open("rb") as model_file:
        observed_hash = hashlib.file_digest(model_file, "sha256").hexdigest()
    final = model_path.stat()
    if (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns) != (
        final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns
    ):
        raise ValueError("model changed during bounded hashing")
    start_after, argv_after, path_after, host_after, key_after = _process(
        proc_root, pid,
    )
    boot_after = str(uuid.UUID(boot_file.read_text().strip()))
    socket_after = _listeners(proc_root, tcp_tables, pid, port)
    if (boot_before, start, argv_before, model_path, host, key_flag, socket_before) != (
        boot_after, start_after, argv_after, path_after, host_after, key_after,
        socket_after,
    ):
        raise ValueError("process, boot or socket changed during observation")
    loopback_only = socket_before == {"loopback"} and host in (
        "127.0.0.1", "localhost", "::1",
    )
    expected_match = (
        observed_hash == expected_sha256 if expected_sha256 is not None else None
    )
    return {
        "schema": "direct-llama-host-exposure-preflight-v1",
        "source_kind": "unsigned_host_local_read_only",
        "observed_at_ms": int(time.time() * 1000),
        "pid": pid,
        "process_start_ticks": start,
        "boot_id_sha256_prefix": hashlib.sha256(
            boot_before.encode("utf-8")
        ).hexdigest()[:12],
        "listener_port": port,
        "listener_bindings": sorted(socket_before),
        "declared_host_is_loopback": host in ("127.0.0.1", "localhost", "::1"),
        "loopback_only": loopback_only,
        "explicit_api_key_flag_present": key_flag,
        "api_authentication_proven": False,
        "model_bytes": initial.st_size,
        "model_sha256": observed_hash,
        "model_digest_independently_pinned": expected_sha256 is not None,
        "model_expected_digest_match": expected_match,
        "process_socket_model_continuity_checked": True,
        "independent_worker_execution_attested": False,
        "authenticated_owner_receipt": False,
        "signed_source_provenance": False,
        "authenticated_gateway_verified": False,
        "can_approve": False,
        "can_renew": False,
        "can_dispatch": False,
        "admission_status": (
            "BLOCKED_NON_LOOPBACK_DIRECT_LISTENER"
            if not loopback_only else "BLOCKED_NO_TRUSTED_GATEWAY_OR_OWNER"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--expected-model-sha256")
    args = parser.parse_args()
    try:
        row = collect(
            pid=args.pid, port=args.port,
            expected_sha256=args.expected_model_sha256,
        )
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print("direct llama admission preflight refused: " +
              type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(row, sort_keys=True))
    # Even a fully local source witness is NOT execution or approval evidence.
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
