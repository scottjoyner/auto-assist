#!/usr/bin/env python3
"""Read-only cron persistence guard for a physical direct llama-server.

Never prints crontab entries, arguments, filesystem paths, user secrets or
script contents. Only bounded files explicitly referenced by the *current
user's* crontab are considered. Textual attribution is not process provenance.
Always returns NO-GO for exposed or unreviewed startup persistence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

MAX_CRONTAB_BYTES = 65_536
MAX_SCRIPT_BYTES = 65_536
MAX_CRON_LINES = 128
MAX_SCRIPTS_PER_ENTRY = 4
SCRIPT_SUFFIXES = {".sh", ".py"}


def _bounded_file(path: Path, *, home: Path) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("candidate script must be an absolute nonsymlink file")
    # Do not traverse outside operator account or follow path components
    # through symlinks; this is a launcher inspection, not a disk scan.
    resolved_home = home.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(resolved_home):
        raise ValueError("candidate script is outside the current user's home")
    if not resolved.is_file() or not 0 < resolved.stat().st_size <= MAX_SCRIPT_BYTES:
        raise ValueError("candidate script missing or exceeds read-only byte budget")
    data = resolved.read_bytes()
    if len(data) > MAX_SCRIPT_BYTES:
        raise ValueError("candidate script grew during inspection")
    return data


def _schedule_and_command(line: str) -> tuple[str, str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        raise ValueError("not an active crontab entry")
    if stripped.startswith("@"):
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            raise ValueError("malformed @schedule")
        return parts[0], parts[1]
    parts = stripped.split(None, 5)
    if len(parts) != 6:
        raise ValueError("malformed periodic cron entry")
    return "periodic", parts[-1]


def _candidate_scripts(command: str, home: Path) -> list[Path]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError("malformed cron shell command") from exc
    found: list[Path] = []
    for token in argv:
        if token.startswith("~/"):
            token = str(home / token[2:])
        if not token.startswith("/"):
            continue
        candidate = Path(token)
        if candidate.suffix not in SCRIPT_SUFFIXES:
            continue
        if candidate not in found:
            found.append(candidate)
    if len(found) > MAX_SCRIPTS_PER_ENTRY:
        raise ValueError("too many candidate scripts in one cron entry")
    return found


def audit_text(
    crontab: str, *, home: Path, listener_port: int,
    cgroup: str = "cron.service",
) -> dict:
    """Classify current-user cron launch risk; no commands/paths returned."""
    if not isinstance(crontab, str) or len(crontab.encode()) > MAX_CRONTAB_BYTES:
        raise ValueError("oversized crontab")
    if type(listener_port) is not int or not 1 <= listener_port <= 65535:
        raise ValueError("invalid port")
    if not isinstance(cgroup, str) or len(cgroup) > 200:
        raise ValueError("invalid cgroup")
    entries = [
        line for line in crontab.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(entries) > MAX_CRON_LINES:
        raise ValueError("too many cron entries")
    findings = []
    other_llama_launchers = []
    unreadable = 0
    boot_count = 0
    periodic_count = 0
    seen_signatures: set[str] = set()
    for entry in entries:
        try:
            schedule, command = _schedule_and_command(entry)
            scripts = _candidate_scripts(command, home)
        except ValueError:
            unreadable += 1
            continue
        for candidate in scripts:
            try:
                raw = _bounded_file(candidate, home=home)
                content = raw.decode("utf-8")
            except (OSError, ValueError, UnicodeDecodeError):
                unreadable += 1
                continue
            # Case-insensitive textual correlation is intentionally a
            # *candidate* only; comments/inactive branches may match.
            if "llama-server" not in content:
                continue
            is_target_launcher = re.search(
                r"(?<!\d)" + re.escape(str(listener_port)) + r"(?!\d)",
                content,
            ) is not None
            sig = hashlib.sha256(raw).hexdigest()
            key = sig + ":" + schedule
            if key in seen_signatures:
                continue
            seen_signatures.add(key)
            if not is_target_launcher:
                # This describes a different *script candidate*, not a
                # proven active server or verified remote exposure.
                other_llama_launchers.append({
                    "schedule_kind": (
                        "@reboot" if schedule == "@reboot" else "periodic"
                    ),
                    "launcher_basename": candidate.name,
                    "launcher_sha256_prefix": sig[:12],
                    "wildcard_host_text_found": bool(re.search(
                        r"--host\\s+[\"']?0\\.0\\.0\\.0",
                        content,
                    )),
                    "current_process_running_proven": False,
                    "listener_exposure_proven": False,
                    "can_approve": False,
                    "can_dispatch": False,
                })
                continue
            boot_count += schedule == "@reboot"
            periodic_count += schedule != "@reboot"
            findings.append({
                "schedule_kind": "@reboot" if schedule == "@reboot" else "periodic",
                "launcher_basename": candidate.name,
                "launcher_sha256_prefix": sig[:12],
                "launch_binary_mentioned": True,
                "listener_port_mentioned": True,
                "contains_api_key_flag": "--api-key" in content,
                # A loopback literal may be used only for a health probe.
                # It is NOT evidence of an actual --host binding.
                "mentions_loopback_literal": any(
                    value in content for value in ("127.0.0.1", "::1", "localhost")
                ),
                "declared_host_loopback_proven": False,
                "proof_of_actual_launch": False,
            })
    persistence_possible = bool(findings)
    return {
        "schema": "direct-llama-cron-persistence-preflight-v1",
        "source_kind": "unsigned_local_current_user_crontab",
        "observed_service_cgroup_is_cron": "cron.service" in cgroup,
        "active_current_user_cron_entries": len(entries),
        "candidate_launcher_entries": len(findings),
        "candidate_reboot_entries": boot_count,
        "candidate_periodic_entries": periodic_count,
        "undecodable_or_unreadable_candidates": unreadable,
        "candidate_launchers": findings,
        "other_llama_launcher_entries": len(other_llama_launchers),
        "other_llama_launchers": other_llama_launchers,
        "other_launcher_current_process_proven": False,
        "relaunch_persistence_possible": persistence_possible,
        "proven_current_process_origin": False,
        "dependencies_completely_inventoried": False,
        "tailnet_access_restricted": False,
        "operator_change_approved": False,
        "can_change_cron": False,
        "can_change_listener": False,
        "can_approve": False,
        "can_dispatch": False,
        "status": (
            "BLOCKED_CRON_PERSISTENCE_CANDIDATE"
            if persistence_possible else "BLOCKED_LAUNCH_SOURCE_UNRESOLVED"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args()
    if type(args.pid) is not int or args.pid <= 0 or args.pid >= 2**31:
        print("startup audit refused: invalid PID", file=sys.stderr)
        return 2
    try:
        proc = Path("/proc") / str(args.pid)
        cgroup = (proc / "cgroup").read_text()
        process_user = (proc / "status").read_text()
        current_uid = os.getuid()
        uid_line = next(
            line for line in process_user.splitlines() if line.startswith("Uid:")
        )
        if int(uid_line.split()[1]) != current_uid:
            raise ValueError("must run as owner of the physical source process")
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, timeout=4, check=True,
        )
        if len(result.stdout) > MAX_CRONTAB_BYTES:
            raise ValueError("crontab output too large")
        report = audit_text(
            result.stdout.decode("utf-8"),
            home=Path.home(), listener_port=args.port, cgroup=cgroup,
        )
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError,
            StopIteration) as exc:
        print("startup audit refused: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1  # Always NO-GO: it is a persistence inventory, not authorization.


if __name__ == "__main__":
    raise SystemExit(main())
