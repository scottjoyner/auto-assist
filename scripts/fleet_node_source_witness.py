#!/usr/bin/env python3
"""Read-only, non-admitting source witness for the existing fleet-node-agent unit.

Never reads secrets, claims tasks, changes service state or asserts that an
on-disk hash proves already-loaded Python bytecode. EXIT 2 means HOLD.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

HEX_SHA = re.compile(r"^[0-9a-f]{64}$")
UNIT = "fleet-node-agent.service"


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _root_from_wrapper(path: Path) -> str:
    """Read only the literal ROOT; do not import or execute the wrapper."""
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    roots = [node.value.value for node in parsed.body
             if isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "ROOT" for t in node.targets)
             and isinstance(node.value, ast.Constant)
             and isinstance(node.value.value, str)]
    imports_agent = any(
        isinstance(n, ast.For) and isinstance(n.iter, (ast.Tuple, ast.List))
        and any(isinstance(x, ast.Constant) and x.value == "fleet_node_agent" for x in n.iter.elts)
        for n in ast.walk(parsed)
    )
    if len(roots) != 1 or not imports_agent or not Path(roots[0]).is_absolute():
        raise ValueError("wrapper_source_root_not_static")
    return roots[0]


def _unit_wrapper_path(text: str, home: Path) -> Path:
    entries = [line.split("=", 1)[1] for line in text.splitlines()
               if line.strip().startswith("ExecStart=")]
    if len(entries) != 1:
        raise ValueError("unit_execstart_not_unique")
    try:
        args = shlex.split(entries[0])
    except ValueError as exc:
        raise ValueError("unit_execstart_unparseable") from exc
    scripts = [a for a in args if a.endswith(".py") and not a.startswith("-")]
    if len(scripts) != 1:
        raise ValueError("unit_wrapper_not_unique")
    candidate = scripts[0].replace("%h", str(home))
    if any(x in candidate for x in ("%", "$", "`", "\n", "..")):
        raise ValueError("unit_wrapper_untrusted_specifier")
    result = Path(candidate)
    if not result.is_absolute():
        raise ValueError("unit_wrapper_not_absolute")
    return result


def _pid_start_epoch(pid: int) -> float:
    proc = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    fields = proc.rsplit(") ", 1)[1].split()
    ticks = int(fields[19])  # Linux /proc/pid/stat field 22 (first here is 3)
    btime = next(int(row.split()[1]) for row in Path("/proc/stat").read_text().splitlines()
                 if row.startswith("btime "))
    return btime + ticks / os.sysconf("SC_CLK_TCK")


def evaluate(*, unit_text: str, wrapper: Path, source_root: str,
             expected_sha: str, pid_started: float, active: bool) -> dict:
    """Return bounded categorical evidence; never authorize task execution."""
    reasons: list[str] = []
    result = {"status": "HOLD", "running_code_attested": False,
              "task_admission": "HOLD", "source_on_disk_matches_candidate": False,
              "candidate_source_sha256": expected_sha}
    if not active:
        reasons.append("unit_not_running")
    try:
        discovered = _unit_wrapper_path(unit_text, Path.home())
        if discovered != wrapper:
            reasons.append("unit_wrapper_not_pinned")
        if _root_from_wrapper(wrapper) != source_root:
            reasons.append("source_root_mismatch")
        source = Path(source_root) / "fleet_node_agent.py"
        for part in (source, *source.parents):
            if part.is_symlink():
                raise ValueError("source_path_symlink")
        if not source.is_file():
            raise ValueError("source_not_found")
        observed = _sha256(source)
        result["source_on_disk_sha256"] = observed
        result["source_on_disk_matches_candidate"] = observed == expected_sha
        if observed != expected_sha:
            reasons.append("source_hash_mismatch")
        if source.stat().st_mtime > pid_started + 2:
            reasons.append("source_changed_after_unit_start")
        if wrapper.stat().st_mtime > pid_started + 2:
            reasons.append("wrapper_changed_after_unit_start")
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        code = str(exc)
        reasons.append(code if code in {
            "unit_execstart_not_unique", "unit_execstart_unparseable", "unit_wrapper_not_unique",
            "unit_wrapper_untrusted_specifier", "unit_wrapper_not_absolute",
            "wrapper_source_root_not_static", "source_path_symlink", "source_not_found"
        } else "witness_input_unavailable")
    reasons.append("running_python_code_cannot_be_attested_from_disk")
    result["reason_codes"] = sorted(set(reasons))
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expected-source-root", required=True)
    p.add_argument("--expected-source-sha256", required=True)
    args = p.parse_args(argv)
    if not HEX_SHA.fullmatch(args.expected_source_sha256):
        p.error("expected SHA-256 must be exactly 64 lowercase hex characters")
    output = {"status": "HOLD", "task_admission": "HOLD",
              "running_code_attested": False, "reason_codes": ["witness_input_unavailable"]}
    try:
        query = subprocess.run(["systemctl", "--user", "show", UNIT, "-pActiveState",
                                "-pMainPID", "-pFragmentPath"],
                               capture_output=True, text=True, timeout=5, check=True)
        props = dict(line.split("=", 1) for line in query.stdout.splitlines() if "=" in line)
        pid = int(props.get("MainPID", "0"))
        if pid <= 0:
            raise ValueError("unit_pid_not_running")
        unit = Path(props["FragmentPath"])
        output = evaluate(unit_text=unit.read_text(encoding="utf-8"),
                          wrapper=Path.home() / "fleet-work/run_fleet_node_agent.py",
                          source_root=args.expected_source_root,
                          expected_sha=args.expected_source_sha256,
                          pid_started=_pid_start_epoch(pid), active=props.get("ActiveState") == "active")
    except (OSError, KeyError, ValueError, subprocess.SubprocessError):
        pass  # No raw stderr, environment, command line or secrets in evidence.
    print(json.dumps(output, sort_keys=True))
    return 2  # Non-success even if on-disk hash matches: NO admission.


if __name__ == "__main__":
    sys.exit(main())
