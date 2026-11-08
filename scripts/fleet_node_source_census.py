#!/usr/bin/env python3
"""Read-only, non-admitting census of a fleet-node-agent's on-disk source.

No credentials, environment values, HTTP requests, model calls, service
changes, or remote writes. Loader/source hashes are *not* an attestation of
Python objects already loaded into process memory; the decision is always HOLD.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

MODULES = ("kv_cache", "recovery_runbooks", "fleet_node_agent")
MAX_FILE_BYTES = 2_000_000


class CensusHold(Exception):
    """Expected fail-closed source-custody boundary."""


def _read_bounded(path: Path) -> tuple[bytes, os.stat_result]:
    if path.is_symlink() or not path.is_file():
        raise CensusHold("missing_or_linked_file")
    before = path.stat()
    if before.st_size > MAX_FILE_BYTES or before.st_size < 1:
        raise CensusHold("invalid_file_size")
    data = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ) or len(data) != before.st_size:
        raise CensusHold("file_changed_while_reading")
    return data, after


def _source_root(loader_data: bytes) -> Path:
    try:
        tree = ast.parse(loader_data, filename="launch-source-only")
    except (UnicodeError, SyntaxError, ValueError) as exc:
        raise CensusHold("unrecognized_loader") from exc
    roots = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ROOT"
            for target in stmt.targets
        ) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            roots.append(stmt.value.value)
    if len(roots) != 1:
        raise CensusHold("unrecognized_loader_root")
    root = Path(roots[0])
    if not root.is_absolute() or ".." in root.parts or root.parts[-2:] != ("src", "assistx"):
        raise CensusHold("untrusted_loader_root")
    # Parse only; never import, execute, or evaluate the launcher.
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    if "importlib.util" not in imports:
        raise CensusHold("unrecognized_loader")
    loops = [
        node for node in tree.body
        if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List))
        and all(isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in node.iter.elts)
        and tuple(item.value for item in node.iter.elts) == MODULES
    ]
    approved_import_loop = any(
        isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and call.func.attr == "spec_from_file_location"
        for loop in loops for call in ast.walk(loop)
    )
    default_caps_empty = any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Attribute) and target.attr == "DEFAULT_CAPS"
                for target in node.targets)
        and isinstance(node.value, ast.List) and len(node.value.elts) == 0
        for node in tree.body
    )
    if not approved_import_loop or not default_caps_empty:
        raise CensusHold("unsupported_launcher_contract")
    return root


def inspect_loader(loader: Path, *, process_start_epoch: float) -> dict:
    loader_bytes, loader_stat = _read_bounded(loader)
    root = _source_root(loader_bytes)
    result = {
        "loader_sha256": hashlib.sha256(loader_bytes).hexdigest(),
        "loader_changed_after_start": loader_stat.st_mtime > process_start_epoch + 2,
        "source_root": str(root),
        "sources": {},
    }
    for module in MODULES:
        data, metadata = _read_bounded(root / f"{module}.py")
        result["sources"][module] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "changed_after_start": metadata.st_mtime > process_start_epoch + 2,
            "legacy_state_ready_marker": module == "fleet_node_agent" and b"state=READY" in data,
            "node_token_marker": module == "fleet_node_agent" and (
                b"X-Fleet-Node-Token" in data or b"FLEET_NODE_TOKEN" in data
            ),
        }
    return result


def _process_start_epoch(pid: int, *, proc: Path = Path("/proc")) -> float:
    try:
        stat = (proc / str(pid) / "stat").read_text()
        ticks = int(stat.rsplit(") ", 1)[1].split()[19])
        uptime = float((proc / "uptime").read_text().split()[0])
    except (IndexError, ValueError, OSError) as exc:
        raise CensusHold("process_start_unavailable") from exc
    return time.time() - uptime + ticks / os.sysconf("SC_CLK_TCK")


def inspect_process(pid: int, *, proc: Path = Path("/proc")) -> dict:
    if not 1 <= pid <= 2**31 - 1:
        raise CensusHold("invalid_pid")
    cmdline = proc / str(pid) / "cmdline"
    try:
        args = [entry.decode("utf-8", "replace") for entry in cmdline.read_bytes().split(b"\0") if entry]
        cwd = Path(os.readlink(proc / str(pid) / "cwd"))
        exe = os.readlink(proc / str(pid) / "exe")
    except OSError as exc:
        raise CensusHold("process_metadata_unavailable") from exc
    # Never print command-line values: they might contain live secrets.
    if len(args) != 2 or not args[1].endswith("/run_fleet_node_agent.py"):
        raise CensusHold("unrecognized_running_command")
    launcher = Path(args[1])
    if not launcher.is_absolute():
        launcher = cwd / launcher
    source = inspect_loader(launcher, process_start_epoch=_process_start_epoch(pid, proc=proc))
    source.update({
        "launcher_basename": launcher.name,
        "runtime_executable_replaced": exe.endswith(" (deleted)"),
        "running_process_present": True,
        "loaded_module_identity_proven": False,
        "task_admission": "HOLD",
        "decision": "HOLD_SOURCE_AND_RUNTIME_NOT_ATTESTED",
    })
    return source


def main() -> int:
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "show", "fleet-node-agent.service", "-p", "MainPID", "--value"],
            capture_output=True, text=True, timeout=4, check=True,
        )
        pid_text = completed.stdout.strip()
        if not pid_text.isdecimal():
            raise CensusHold("no_running_service_pid")
        result = inspect_process(int(pid_text))
        result["status"] = "OBSERVED_NON_ADMITTING"
        print(json.dumps(result, sort_keys=True))
        return 0
    except (CensusHold, OSError, subprocess.SubprocessError) as exc:
        # Error codes are categorical; never print secret-looking exception text.
        code = str(exc) if isinstance(exc, CensusHold) else "observation_failed"
        print(json.dumps({"status": "HOLD", "reason": code, "task_admission": "HOLD",
                          "loaded_module_identity_proven": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
