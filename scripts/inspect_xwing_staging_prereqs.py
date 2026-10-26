#!/usr/bin/env python3
"""Read-only Xwing staging prerequisites. This NEVER imports or runs an image.

An image digest passed by the release custodian is necessary but not proof of
signature validity, owner approval or safe physical restoration. It is not
permitted to copy, fetch, load, build or replace fleet services in this check.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

EXPECTED_HOST = "xwing"
RESERVED_PORT = "18765"
MIN_FREE_BYTES = 10 * 1024 ** 3
MIN_AVAILABLE_KB = 3 * 1024 ** 2


class PreflightHold(Exception):
    pass


def read_only_command(args: list[str]) -> str:
    try:
        p = subprocess.run(
            args, check=False, timeout=10, capture_output=True, text=True
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreflightHold("READ_ONLY_COMMAND_UNAVAILABLE") from None
    if p.returncode != 0 or len(p.stdout) > 4096:
        # Never echo subprocess stderr, which can include machine-specific data.
        raise PreflightHold("READ_ONLY_COMMAND_FAILED") from None
    return p.stdout.strip()


def memory_available_kb(raw: str) -> int:
    for line in raw.splitlines():
        if line.startswith("MemAvailable:"):
            match = re.fullmatch(r"MemAvailable:\s+(\d+)\s+kB", line.strip())
            if match:
                return int(match.group(1))
    raise PreflightHold("MEMORY_PROVENANCE_UNAVAILABLE")


def require_digest(digest: str) -> str:
    if re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is None:
        raise PreflightHold("INVALID_PINNED_IMAGE_ID")
    return digest


def assess(values: dict[str, object]) -> dict[str, object]:
    problems: list[str] = []
    if values["hostname"] != EXPECTED_HOST:
        problems.append("WRONG_HOST")
    if values["remote_docker_override"]:
        problems.append("REMOTE_DOCKER_CONTEXT")
    if values["docker_context"] != "default":
        problems.append("NONDEFAULT_DOCKER_CONTEXT")
    if not values["docker_reachable"]:
        problems.append("DOCKER_NOT_REACHABLE")
    if not values["image_matches_pin"]:
        problems.append("SIGNED_IMAGE_NOT_CACHED_AS_EXPECTED")
    if not values["platform_matches"]:
        problems.append("WRONG_IMAGE_PLATFORM")
    if values["disk_free_bytes"] < MIN_FREE_BYTES:
        problems.append("INSUFFICIENT_DOCKER_SPACE")
    if values["memory_available_kb"] < MIN_AVAILABLE_KB:
        problems.append("INSUFFICIENT_MEMORY")
    if values["port_in_use"]:
        problems.append("RESERVED_PORT_BUSY")
    return {
        "verdict": (
            "XWING_READ_ONLY_STAGING_PREREQUISITES_PASS_NOT_RELEASE_AUTHORITY"
            if not problems else "XWING_STAGING_HOLD"
        ),
        "hold_reasons": problems,
        "host_identity_verified": values["hostname"] == EXPECTED_HOST,
        "local_default_docker_only": not values["remote_docker_override"]
                                    and values["docker_context"] == "default",
        "image_identity_verified_locally": bool(values["image_matches_pin"]),
        "image_platform_verified": bool(values["platform_matches"]),
        "minimum_free_disk_verified": values["disk_free_bytes"] >= MIN_FREE_BYTES,
        "minimum_available_memory_verified": values["memory_available_kb"] >= MIN_AVAILABLE_KB,
        "reserved_port_unbound": not values["port_in_use"],
        "operator_release_approved": False,
        "independent_signature_verified_on_host": False,
        "production_image_or_service_changed": False,
        "isolated_canary_executed": False,
        "physical_rollback_verified": False,
    }


def preflight(digest: str) -> dict[str, object]:
    digest = require_digest(digest)
    host = socket.gethostname().split(".")[0]
    if host != EXPECTED_HOST:
        raise PreflightHold("WRONG_HOST")
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise PreflightHold("REMOTE_DOCKER_OVERRIDE_PRESENT")
    context = read_only_command(["docker", "context", "show"])
    server = read_only_command(["docker", "info", "--format", "{{.ServerVersion}}"])
    if not server or len(server) > 128:
        raise PreflightHold("DOCKER_NOT_REACHABLE")
    disk = shutil.disk_usage("/var/lib/docker")
    mem = memory_available_kb(Path("/proc/meminfo").read_text())
    listeners = read_only_command(["ss", "-H", "-ltn", f"( sport = :{RESERVED_PORT} )"])
    # Do not output any listener address.
    try:
        identity = read_only_command(["docker", "image", "inspect", "--format",
                                      "{{.Id}}", digest])
        platform = read_only_command(["docker", "image", "inspect", "--format",
                                      "{{.Os}}/{{.Architecture}}", digest])
    except PreflightHold:
        identity = ""
        platform = ""
    return assess({
        "hostname": host,
        "remote_docker_override": False,
        "docker_context": context,
        "docker_reachable": bool(server),
        "image_matches_pin": identity == digest,
        "platform_matches": platform == "linux/amd64",
        "disk_free_bytes": disk.free,
        "memory_available_kb": mem,
        "port_in_use": bool(listeners),
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", required=True)
    parser.add_argument("--expected-image", required=True)
    args = parser.parse_args()
    try:
        receipt = preflight(args.expected_image)
        print(json.dumps(receipt, sort_keys=True))
        return 0 if not receipt["hold_reasons"] else 2
    except (PreflightHold, OSError, ValueError) as e:
        # Never expose source paths, environ, credentials or raw exception text.
        code = str(e) if isinstance(e, PreflightHold) else "HOST_EVIDENCE_UNAVAILABLE"
        print(json.dumps({
            "verdict": "XWING_STAGING_HOLD",
            "hold_reasons": [code],
            "operator_release_approved": False,
            "production_image_or_service_changed": False,
            "isolated_canary_executed": False,
            "physical_rollback_verified": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
