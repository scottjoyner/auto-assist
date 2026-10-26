#!/usr/bin/env python3
"""Beelink recovery guard entrypoint preflight: inspect only, never execute.

This is not a memory admission test, a service restart, or authorization to
change protected recovery checkout files. No unit environment is read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

UNIT = "assistx-recovery-memory-guard.service"
SCRIPT = "recovery-memory-guard.py"
MAX_UNIT_BYTES = 16_384


class Hold(Exception):
    pass


def _values(properties: str) -> dict[str, str]:
    if not isinstance(properties, str) or len(properties) > MAX_UNIT_BYTES:
        raise Hold("invalid_unit_properties")
    output: dict[str, str] = {}
    for line in properties.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key not in ("WorkingDirectory", "ExecStart", "User"):
            continue
        if key in output:
            raise Hold("duplicate_unit_property")
        output[key] = value
    if "WorkingDirectory" not in output or "ExecStart" not in output:
        raise Hold("missing_unit_property")
    return output


def _entrypoint(properties: dict[str, str]) -> Path:
    cwd = properties["WorkingDirectory"]
    if not cwd or cwd.startswith("-") or not Path(cwd).is_absolute() or ".." in Path(cwd).parts:
        raise Hold("invalid_working_directory")
    raw = properties["ExecStart"]
    # systemctl show normally renders argv[] and path inside a struct.
    match = re.search(r"(?:^|[;\s])argv\[\]=([^;}]+)", raw)
    if match is None:
        raise Hold("argv_unavailable")
    try:
        argv = shlex.split(match.group(1))
    except ValueError:
        raise Hold("malformed_argv") from None
    if not 2 <= len(argv) <= 32:
        raise Hold("unexpected_argv_shape")
    executable = Path(argv[0]).name
    if executable not in ("python", "python3", "python3.11", "python3.12", "python3.13"):
        raise Hold("unexpected_launcher")
    scripts = [arg for arg in argv[1:] if arg.endswith(".py")]
    if len(scripts) != 1 or Path(scripts[0]).name != SCRIPT:
        raise Hold("unexpected_script_identity")
    script = Path(scripts[0])
    if ".." in script.parts:
        raise Hold("parent_traversal_refused")
    if not script.is_absolute():
        script = Path(cwd) / script
    return script


def _inspect_path(path: Path) -> str:
    # No resolving symlinks and no protected source or environment reads.
    for candidate in reversed((path, *path.parents)):
        try:
            info = candidate.lstat()
        except PermissionError:
            return "PROTECTED_PATH_UNVERIFIABLE"
        except FileNotFoundError:
            return "ENTRYPOINT_PATH_MISSING"
        except OSError:
            return "ENTRYPOINT_METADATA_UNAVAILABLE"
        if stat.S_ISLNK(info.st_mode):
            return "SYMLINKED_ENTRYPOINT_PATH"
    try:
        s = path.stat()
        if not stat.S_ISREG(s.st_mode):
            return "ENTRYPOINT_NOT_REGULAR_FILE"
        if not os.access(path.parent, os.X_OK) or not os.access(path, os.R_OK):
            return "ENTRYPOINT_UNREADABLE_TO_CALLER"
        if s.st_mode & 0o022:
            return "ENTRYPOINT_GROUP_OR_WORLD_WRITABLE"
    except PermissionError:
        return "PROTECTED_PATH_UNVERIFIABLE"
    except OSError:
        return "ENTRYPOINT_METADATA_UNAVAILABLE"
    return "ENTRYPOINT_PRESENT_ONLY"


def inspect_unit(properties: str) -> dict[str, object]:
    report: dict[str, object] = {
        "schema": "beelink-recovery-guard-entrypoint-preflight/v1",
        "verdict": "HOLD",
        "reason": "UNVERIFIED",
        "unit": UNIT,
        "script_basename": SCRIPT,
        "service_restarted": False,
        "guard_executed": False,
        "memory_admission_proven": False,
        "source_commit_verified": False,
        "privileged_access_performed": False,
        "deployment_authorized": False,
    }
    try:
        values = _values(properties)
        target = _entrypoint(values)
        report["reason"] = _inspect_path(target)
        if report["reason"] == "ENTRYPOINT_PRESENT_ONLY":
            report["verdict"] = "ENTRYPOINT_METADATA_ONLY"
    except Hold as err:
        report["reason"] = str(err)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Beelink guard entrypoint diagnostics")
    parser.add_argument("--unit", default=UNIT, choices=(UNIT,))
    args = parser.parse_args()
    try:
        proc = subprocess.run(
            ["systemctl", "show", args.unit, "--no-pager",
             "-p", "ExecStart", "-p", "WorkingDirectory", "-p", "User"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
        if proc.returncode or len(proc.stdout) > MAX_UNIT_BYTES:
            result = inspect_unit("")
        else:
            result = inspect_unit(proc.stdout)
    except (OSError, subprocess.TimeoutExpired):
        result = inspect_unit("")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "ENTRYPOINT_METADATA_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
