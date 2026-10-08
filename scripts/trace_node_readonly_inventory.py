#!/usr/bin/env python3
"""Read-only fleet-node trace execution metadata witness (never an admission test).

No HTTP calls, no keys or tokens read into output, no filesystem mutations.
UNKNOWN is never a pass. No physical authorization is inferred from this report.
Compatible with Python 3.9+ on Linux and macOS. Do not run as root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "assistx.trace-node-readonly-inventory.v1"
FLAGS = (
    "FLEET_TRACE_PROBE_ENABLED",
    "FLEET_TRACE_REAL_EXECUTION_ENABLED",
    "FLEET_UNSAFE_SHELL_TASKS_ENABLED",
    "ASSISTX_TRACE_LEASE_ISSUER_ENABLED",
)
KEY_FLAGS = (
    "FLEET_TRACE_LEASE_VERIFIER_KEY_FILE",
    "ASSISTX_TRACE_LEASE_SIGNING_KEY_FILE",
)
SENSITIVE_PRESENT = (
    "FLEET_NODE_AUTH_TOKEN",
    "FLEET_TRACE_ISSUER_ORIGIN",
)
MAX_JOURNAL_HASH_BYTES = 16 * 1024 * 1024


def mask_flag(raw):
    """Never serialize environment variable values or paths."""
    if raw is None:
        return "unobserved"
    x = raw.strip().lower()
    if x == "false":
        return "false"
    if x == "true":
        return "true"
    return "other"


def metadata(path, *, require_private=False):
    """lstat only; never dereference a key symlink or open key bytes."""
    if not path:
        return {"status": "not_configured"}
    try:
        s = os.lstat(path)
    except (OSError, ValueError):
        return {"status": "unavailable"}
    regular = stat.S_ISREG(s.st_mode)
    directory = stat.S_ISDIR(s.st_mode)
    result = {
        "status": "observed",
        "mode": format(stat.S_IMODE(s.st_mode), "04o"),
        "owned_by_observer": s.st_uid == os.getuid(),
        "regular_file": regular,
        "directory": directory,
        "symlink": stat.S_ISLNK(s.st_mode),
        "link_count": s.st_nlink,
        "size_bytes": s.st_size if regular else None,
    }
    if require_private:
        result["owner_private_regular"] = (
            regular and s.st_uid == os.getuid()
            and stat.S_IMODE(s.st_mode) & 0o077 == 0 and s.st_nlink == 1
        )
    return result


def journal_metadata(root):
    if not root:
        return {"status": "not_configured"}
    base = metadata(root)
    path = Path(root) / "journal.jsonl"
    if (
        base.get("status") != "observed"
        or not base.get("directory")
        or not base.get("owned_by_observer")
        or base.get("symlink")
        or int(base.get("mode", "0777"), 8) & 0o077
    ):
        return {"root": base, "journal": {"status": "unavailable_or_unsafe_root"}}
    info = metadata(str(path))
    result = {"root": base, "journal": info}
    if info.get("status") == "observed" and info.get("regular_file") and not info.get("symlink"):
        size = info["size_bytes"]
        if size > MAX_JOURNAL_HASH_BYTES:
            result["journal_hash_status"] = "too_large_not_read"
        else:
            try:
                digest = hashlib.sha256()
                rows = 0
                with path.open("rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        rows += chunk.count(b"\n")
                        digest.update(chunk)
                result["journal_sha256"] = digest.hexdigest()
                result["journal_newline_count"] = rows
                result["journal_hash_status"] = "observed"
            except OSError:
                result["journal_hash_status"] = "unavailable"
    return result


def _role(cmd):
    cmd = cmd.lower()
    if "fleet_node_agent" in cmd:
        return "fleet_worker"
    if "assistx.api" in cmd or ("uvicorn" in cmd and "assistx" in cmd):
        return "assistx_api"
    return None


def scan_linux():
    output = []
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdecimal() or len(output) >= 64:
            continue
        try:
            raw = (pid_dir / "cmdline").read_bytes()
            role = _role(raw.replace(b"\0", b" ").decode("utf-8", "replace"))
            if role is None:
                continue
            # Access is limited to processes visible to the observing account.
            environ = (pid_dir / "environ").read_bytes()
            entries = {}
            for chunk in environ.split(b"\0"):
                key, sep, value = chunk.partition(b"=")
                if sep and key.decode("ascii", "ignore") in (*FLAGS, *KEY_FLAGS, *SENSITIVE_PRESENT,
                                                                "FLEET_TRACE_EXECUTION_AUDIT_ROOT"):
                    entries[key.decode("ascii")] = value.decode("utf-8", "replace")
            output.append(safe_process_record(int(pid_dir.name), role, entries))
        except (OSError, PermissionError, ValueError):
            output.append({"role": "inaccessible_process", "environment": "unobserved"})
    return output


def safe_process_record(pid, role, values):
    return {
        "pid": pid,
        "role": role,
        "flag_states": {key: mask_flag(values.get(key)) for key in FLAGS},
        "prerequisites_configured": {
            key: bool(values.get(key)) for key in SENSITIVE_PRESENT
        },
        "key_metadata": {
            key: metadata(values.get(key), require_private=(key == KEY_FLAGS[1]))
            for key in KEY_FLAGS
        },
        "audit": journal_metadata(values.get("FLEET_TRACE_EXECUTION_AUDIT_ROOT")),
    }


def scan_macos():
    """macOS ps does not give trustworthy service env; do not pretend it does."""
    try:
        p = subprocess.run(
            ["ps", "-A", "-o", "pid=", "-o", "args="],
            capture_output=True, text=True, timeout=6, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return [{"role": "process_enumeration_unavailable", "environment": "unobserved"}]
    output = []
    for line in p.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(.*)", line)
        if not match:
            continue
        role = _role(match.group(2))
        if role:
            output.append({
                "pid": int(match.group(1)), "role": role, "environment": "unobserved",
                "flag_states": {key: "unobserved" for key in FLAGS},
                "key_metadata": {key: {"status": "unobserved"} for key in KEY_FLAGS},
            })
    return output[:64]


def tailscale_identity():
    try:
        p = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                           text=True, timeout=6, check=True)
        obj = json.loads(p.stdout)
        me = obj.get("Self") or {}
        return {"hostname": me.get("HostName"), "dns_name": me.get("DNSName"),
                "online": me.get("Online")}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"status": "unavailable"}


def nas_policy():
    if platform.system() != "Linux":
        return {"status": "not_inspected_on_platform"}
    try:
        text = Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        return {"status": "unknown"}
    matches = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "/nas":
            opts = dict(part.split("=", 1) for part in fields[3].split(",") if "=" in part)
            matches.append({
                "filesystem": fields[2],
                "mode_file": opts.get("file_mode"),
                "mode_dir": opts.get("dir_mode"),
                "nounix": "nounix" in fields[3].split(","),
                "noperm": "noperm" in fields[3].split(","),
            })
    return {"status": "observed" if matches else "mount_not_observed", "mounts": matches}


def collect(expected_node, audit_root, release_root):
    ident = tailscale_identity()
    dns = str(ident.get("dns_name") or "").rstrip(".").split(".")[0]
    identity_matches = dns == expected_node
    system = platform.system()
    processes = scan_linux() if system == "Linux" else scan_macos() if system == "Darwin" else []
    return {
        "schema": SCHEMA, "observed_at": datetime.now(timezone.utc).isoformat(),
        "expected_node": expected_node, "hostname": platform.node(),
        "platform": system, "observer_is_root": os.geteuid() == 0,
        "tailscale_identity": ident,
        "tailnet_identity_matches_expected": identity_matches,
        "processes": processes,
        "process_inventory_complete": False,
        "release_root": metadata(release_root),
        "shadow_audit": journal_metadata(audit_root),
        "nas": nas_policy(),
        "physical_authenticated_negative_admission": "not_tested",
        "claim_issued": False, "dispatch_authorized": False,
        "production_promotion_eligible": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-node", required=True,
                        choices=["xwing", "scotts-macbook-air"])
    parser.add_argument("--audit-root", required=True)
    parser.add_argument("--release-root", required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.expected_node, args.audit_root, args.release_root),
                     sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
