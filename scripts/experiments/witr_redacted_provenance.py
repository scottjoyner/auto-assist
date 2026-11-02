#!/usr/bin/env python3
"""No-secret, read-only witr --short --json process ancestry adapter.
No command-line arguments, env, sockets, paths, or arbitrary upstream text
ever leave this adapter. This is not a routing/admission authority.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

MAX_BYTES = 262144
MAX_DEPTH = 32
CATEGORIES = {"llama-server": "inference-server", "python": "python-worker",
              "python3": "python-worker", "systemd": "init-supervisor",
              "sshd": "ssh-service", "bash": "shell", "sh": "shell",
              "node": "node-runtime", "docker": "container-engine",
              "containerd": "container-engine"}

def positive_pid(value, name, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= 2147483647:
        raise ValueError(name + " invalid")
    return value

def category(pid):
    try:
        with open("/proc/%d/comm" % pid, "rb") as fh:
            name = fh.read(80).decode("ascii", "ignore").strip()
    except (OSError, UnicodeError):
        return "unknown"
    return CATEGORIES.get(name, "other")

def scrub_short(raw, target_pid, inspect_local=False):
    target_pid = positive_pid(target_pid, "target_pid")
    if type(raw) is not list or not 1 <= len(raw) <= MAX_DEPTH:
        raise ValueError("short JSON must be a bounded array")
    clean = []
    seen = set()
    for item in raw:
        if type(item) is not dict:
            raise ValueError("ancestry entry must be object")
        pid = positive_pid(item.get("PID"), "PID")
        if pid in seen:
            raise ValueError("duplicate PID")
        seen.add(pid)
        ppid = item.get("PPID")
        if ppid is not None:
            ppid = positive_pid(ppid, "PPID", zero=True)
        exited = item.get("ParentExited", False)
        if type(exited) is not bool:
            raise ValueError("ParentExited must be boolean")
        clean.append({"pid": pid, "reported_ppid": ppid,
                      "reported_parent_exited": exited,
                      "category": category(pid) if inspect_local else "not_inspected"})
    if clean[-1]["pid"] != target_pid:
        raise ValueError("target PID mismatch")
    return {"kind": "witr_redacted_process_provenance_v1", "target_pid": target_pid,
            "source": "pranshuparmar/witr --short --json",
            "ancestry": clean, "full_commands_exported": False,
            "environment_exported": False, "socket_addresses_exported": False,
            "physical_runtime_verified": False, "claim_verified": False,
            "dispatch_allowed": False,
            "note": "Best-effort diagnostic only; do not infer restart permission, model loading, or active inference."}

def json_loads(s):
    return json.loads(s, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")))

def probe(binary, checksum, pid):
    if os.geteuid() == 0:
        raise ValueError("run as non-root")
    if re.fullmatch("[0-9a-fA-F]{64}", checksum) is None:
        raise ValueError("pinned sha256 required")
    if not binary.is_absolute() or not binary.is_file() or binary.stat().st_size > 30000000:
        raise ValueError("invalid binary")
    if hashlib.sha256(binary.read_bytes()).hexdigest() != checksum.lower():
        raise ValueError("checksum mismatch")
    try:
        result = subprocess.run([str(binary), "--pid", str(pid), "--short", "--json"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, check=False, timeout=7,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("probe unavailable") from None
    if result.returncode not in (0, 1, 6) or len(result.stdout) > MAX_BYTES:
        raise ValueError("bad probe exit or output size")
    return json_loads(result.stdout.decode("utf-8"))

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pid", type=int, required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixture", type=Path)
    src.add_argument("--witr-bin", type=Path)
    p.add_argument("--witr-sha256", default="")
    a = p.parse_args(argv)
    try:
        pid = positive_pid(a.pid, "pid")
        if a.fixture is not None:
            if not a.fixture.is_file() or a.fixture.stat().st_size > MAX_BYTES:
                raise ValueError("fixture too large or absent")
            raw = json_loads(a.fixture.read_text(encoding="utf-8"))
        else:
            raw = probe(a.witr_bin, a.witr_sha256, pid)
        report = scrub_short(raw, pid, inspect_local=a.witr_bin is not None)
    except (ValueError, OSError, UnicodeError):
        print("witr provenance rejected input or failed verification", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
