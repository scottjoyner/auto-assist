#!/usr/bin/env python3
"""Non-admitting, explicitly opt-in host-key-verified physical-LAN SSH model witness.

It executes ONLY a fixed GET /v1/models via an SSH stdio stream to remote
127.0.0.1:1234 for two explicitly allowlisted nodes. No listener, tunnel,
model generation, Task request, graph write, approval or unsigned fallback.
Exit 2 intentionally means fleet admission remains HOLD, even on success.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import select
import signal
import subprocess
import time
from collections.abc import Callable
from typing import Any

TARGETS: dict[str, tuple[str, str]] = {
    "xwing": ("192.168.1.86", "ornith-1.5-35b-a3b-apex-mtp"),
    "joyner": ("192.168.1.79", "joyner-toolcall"),
}
REQUEST = (
    b"GET /v1/models HTTP/1.1\r\n"
    b"Host: localhost\r\n"
    b"Connection: close\r\n"
    b"Accept: application/json\r\n\r\n"
)
MAX_WIRE = 64 * 1024
MAX_HEADER = 8 * 1024
MAX_MODELS = 256
DEADLINE_SECONDS = 9.0


def ssh_command(node: str) -> list[str]:
    """No remote shell, no proxy config, no agent forward, no loose host key."""
    if node not in TARGETS:
        raise ValueError("node_not_in_reviewed_allowlist")
    host, _ = TARGETS[node]
    return [
        "ssh", "-T", "-F", "/dev/null",
        "-o", f"HostName={host}",
        "-o", f"HostKeyAlias={node}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "ForwardAgent=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "ProxyCommand=none",
        "-o", "CanonicalizeHostname=no",
        "-o", "ConnectTimeout=3",
        "-o", "ConnectionAttempts=1",
        "-o", "ServerAliveInterval=3",
        "-o", "ServerAliveCountMax=1",
        "-W", "127.0.0.1:1234", node,
    ]


def parse_http_catalog(data: bytes, expected_model: str) -> int:
    """Accept only a complete bounded HTTP/1.1 JSON model response."""
    if not isinstance(data, bytes) or len(data) > MAX_WIRE:
        raise ValueError("wire_response_oversized")
    header, separator, body = data.partition(b"\r\n\r\n")
    if not separator or not body or len(header) > MAX_HEADER:
        raise ValueError("invalid_http_framing")
    lines = header.split(b"\r\n")
    if lines[0] != b"HTTP/1.1 200 OK":
        raise ValueError("http_not_200")
    fields: dict[str, str] = {}
    for raw in lines[1:]:
        if b":" not in raw:
            raise ValueError("invalid_http_header")
        key, _, val = raw.partition(b":")
        try:
            k = key.decode("ascii").lower()
            v = val.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("non_ascii_header") from exc
        if not re.fullmatch(r"[a-z0-9-]{1,70}", k) or k in fields:
            raise ValueError("duplicate_or_invalid_header")
        fields[k] = v
    if not fields.get("content-type", "").lower().startswith("application/json"):
        raise ValueError("unexpected_content_type")
    if "transfer-encoding" in fields:
        raise ValueError("transfer_encoding_not_supported")
    try:
        size = int(fields["content-length"])
    except (ValueError, KeyError) as exc:
        raise ValueError("missing_or_invalid_content_length") from exc
    if size < 0 or size > MAX_WIRE - MAX_HEADER or size != len(body):
        raise ValueError("body_length_mismatch")

    def strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate_json_property")
            obj[key] = value
        return obj

    try:
        catalog = json.loads(body, object_pairs_hook=strict_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json_catalog") from exc
    if not isinstance(catalog, dict) or not isinstance(catalog.get("data"), list):
        raise ValueError("invalid_model_catalog")
    items = catalog["data"]
    if len(items) > MAX_MODELS:
        raise ValueError("model_catalog_too_large")
    models = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("malformed_model_entry")
        model_id = item.get("id")
        if not isinstance(model_id, str) or not 0 < len(model_id) <= 256:
            raise ValueError("invalid_model_identity")
        models.add(model_id)
    if expected_model not in models:
        raise ValueError("exact_expected_model_missing")
    return len(models)


def read_ssh_catalog(node: str) -> bytes:
    """Read a capped SSH stream; forcibly clean up a timed-out child group."""
    command = ssh_command(node)
    proc = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(REQUEST)
        proc.stdin.close()
        limit = time.monotonic() + DEADLINE_SECONDS
        data = bytearray()
        while True:
            remaining = limit - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ssh_catalog_deadline_exceeded")
            ready, _, _ = select.select([proc.stdout], [], [], remaining)
            if not ready:
                raise TimeoutError("ssh_catalog_deadline_exceeded")
            fragment = os.read(proc.stdout.fileno(), min(8192, MAX_WIRE + 1 - len(data)))
            if not fragment:
                break
            data.extend(fragment)
            if len(data) > MAX_WIRE:
                raise ValueError("wire_response_oversized")
        if proc.wait(timeout=max(0.1, limit - time.monotonic())) != 0:
            raise ValueError("ssh_verified_transport_failed")
        return bytes(data)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=1)
        if proc.stdout:
            proc.stdout.close()


def check_node(node: str, reader: Callable[[str], bytes] = read_ssh_catalog) -> dict[str, Any]:
    if node not in TARGETS:
        raise ValueError("node_not_in_reviewed_allowlist")
    ip, model = TARGETS[node]
    model_count = parse_http_catalog(reader(node), model)
    return {
        "node": node, "source_transport": "verified_ssh_over_physical_lan",
        "remote_destination": "127.0.0.1:1234",
        "model_catalog_http": 200, "expected_model_present": True,
        "advertised_unique_models": model_count,
        "direct_lan_http_verified": False,
        "production_ingress_authorized": False,
        "approved_parallel_slots": 0,
        "task_admission": "HOLD", "status": "OBSERVED_NON_ADMITTING",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Run actual bounded SSH catalog probes")
    parser.add_argument("--node", choices=("xwing", "joyner", "all"), default="all")
    args = parser.parse_args(argv)
    report: dict[str, Any] = {
        "schema": "fleet-ssh-lan-nonadmitting-witness-v1",
        "task_admission": "HOLD", "admission_applied": False,
        "direct_lan_http_verified": False, "status": "HOLD", "observations": [],
    }
    if not args.live:
        report["reason"] = "LIVE_SSH_PROBES_NOT_REQUESTED"
    else:
        targets = list(TARGETS) if args.node == "all" else [args.node]
        for node in targets:
            try:
                report["observations"].append(check_node(node))
            except (OSError, TimeoutError, ValueError, subprocess.SubprocessError):
                report["observations"].append({"node": node, "status": "HOLD",
                                               "reason": "SSH_LAN_MODEL_WITNESS_FAILED",
                                               "task_admission": "HOLD"})
    print(json.dumps(report, sort_keys=True))
    return 2  # Physical observation is NOT authority to dispatch a Task.


if __name__ == "__main__":
    raise SystemExit(main())
