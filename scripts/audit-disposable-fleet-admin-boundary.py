#!/usr/bin/env python3
"""Read-only, deny-only audit of *operator reachability* across candidate hosts.

No trust roots, durable monotonic state, role promotions, signatures, secrets,
container launches, file writes, or production network changes. All reachable
hosts under this operator's SSH + Docker access are a shared control plane
for this experiment, NOT independent governance. Inaccessible hosts are
UNKNOWN, never certified safe. This tool can NEVER return release GO.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

_ALIAS = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,61}$")
_REMOTE_CMD = (
    "printf 'MARKER=FLEET_READ_ONLY_AUTHORITY_V1\\n'; "
    "printf 'UID=%s\\n' \"$(id -u)\"; "
    "printf 'USER=%s\\n' \"$(id -un)\"; "
    "if command -v docker >/dev/null 2>&1 "
    "&& timeout 3s docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; "
    "then printf 'DOCKER_ACCESS=1\\n'; else printf 'DOCKER_ACCESS=0\\n'; fi"
)
_SSH_ARGS = (
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=4",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "NumberOfPasswordPrompts=0",
    "-o", "ConnectionAttempts=1",
)
_ALLOWED_REMOTE_KEYS = frozenset({"MARKER", "UID", "USER", "DOCKER_ACCESS"})


def _parse_status(stdout: str) -> dict[str, str] | None:
    if len(stdout) > 400 or "\x00" in stdout:
        return None
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if line.count("=") != 1:
            return None
        key, value = line.split("=", 1)
        if key not in _ALLOWED_REMOTE_KEYS or key in fields or not value:
            return None
        fields[key] = value
    if set(fields) != _ALLOWED_REMOTE_KEYS or fields["MARKER"] != "FLEET_READ_ONLY_AUTHORITY_V1":
        return None
    if not fields["UID"].isdigit() or len(fields["UID"]) > 10:
        return None
    if not _ALIAS.fullmatch(fields["USER"]):
        return None
    if fields["DOCKER_ACCESS"] not in {"0", "1"}:
        return None
    return fields


def audit(
    aliases: Sequence[str],
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    # Explicit alias allowlist is required; never interpolate a user string
    # into remote shell. Remote command is fixed and non-mutating.
    if (
        not isinstance(aliases, (list, tuple))
        or not 1 <= len(aliases) <= 6
        or len(set(aliases)) != len(aliases)
        or any(not isinstance(x, str) or not _ALIAS.fullmatch(x) for x in aliases)
    ):
        return {
            "schema_version": 1,
            "verdict": "UNKNOWN_NO_RELEASE_AUTHORITY",
            "reason": "invalid_alias_scope",
            "nodes": [],
        }
    results = []
    for alias in aliases:
        argv = ["ssh", *_SSH_ARGS, "--", alias, _REMOTE_CMD]
        try:
            process = runner(
                argv, capture_output=True, text=True, check=False, timeout=10,
            )
            payload = _parse_status(process.stdout) if process.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            payload = None
        if payload is None:
            results.append({"alias": alias, "operator_reachable": None,
                            "docker_admin_accessible": None})
        else:
            results.append({
                "alias": alias,
                "operator_reachable": True,
                "docker_admin_accessible": payload["DOCKER_ACCESS"] == "1",
            })

    # Two nodes independently self-reporting Docker access through the same
    # caller prove overlapping *control by this operator*, not separate
    # administrator custody. A single reachable host cannot establish
    # independence either.
    shared = sum(x["docker_admin_accessible"] is True for x in results) >= 2
    return {
        "schema_version": 1,
        "verdict": (
            "NO_GO_SHARED_OPERATOR_DOCKER_REACHABILITY"
            if shared else "UNKNOWN_NO_RELEASE_AUTHORITY"
        ),
        "reason": (
            "current_operator_can_administer_multiple_hosts"
            if shared else "independent_admin_custody_not_established"
        ),
        "nodes": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only cross-host operator reachability audit; never authorizes release."
    )
    parser.add_argument("aliases", nargs="+", help="Explicit SSH aliases, max six")
    args = parser.parse_args(argv)
    result = audit(args.aliases)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    # Every possible report deliberately exits nonzero: it is NOT an
    # admission verifier or standalone positive release readiness gate.
    return 2 if result["verdict"].startswith("NO_GO") else 3


if __name__ == "__main__":
    raise SystemExit(main())
