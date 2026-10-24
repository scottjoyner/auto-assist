#!/usr/bin/env python3
"""Read-only x1-370 vs Beelink storage-mount comparison; never mount or copy.

Uses existing strict SSH host-key verification, one bounded read-only
remote findmnt command, and local findmnt. No secrets or file contents.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess

LOCAL = "/media/scott/NAS5"
REMOTE = ("/nas", "/mnt/nas5-output-primary",
          "/mnt/nas5-output-direct", "/media/scott/NAS5")
EXPECTED = {"/nas": "btrfs",
            "/mnt/nas5-output-primary": "exfat",
            "/mnt/nas5-output-direct": "exfat"}
BEELINK_ALIAS = "beelink"
SSH = ["ssh", "-n", "-T", "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=yes",
       "-o", "ConnectTimeout=4", "-o", "ConnectionAttempts=1"]
REMOTE_SCRIPT = (
    'set -eu; for p in /nas /mnt/nas5-output-primary '
    '/mnt/nas5-output-direct /media/scott/NAS5; do '
    '[ -d "$p" ] || { printf "%s MISSING MISSING MISSING\\n" "$p"; continue; }; '
    'printf "%s " "$p"; findmnt -T "$p" -n -o TARGET,SOURCE,FSTYPE; done'
)
BLOCKERS = (
    "x1_370_label_is_not_a_verified_nas_mount",
    "remote_beelink_topology_not_independent_administrative_custody",
    "nas5_data_provenance_and_local_fallback_contents_unverified",
    "no_owner_authorized_read_write_source_or_custody_grant",
    "production_mounts_and_revocation_rollback_unremediated",
)

class UnsafeCrossHost(ValueError):
    pass

def require(ok: bool, reason: str):
    if not ok:
        raise UnsafeCrossHost(reason)

def parse_mount_line(line: str) -> tuple[str, str, str, str]:
    # Fixed four bare fields; reject control text or untrusted path characters.
    require(len(line) < 512 and not any(c in line for c in (chr(0),chr(13),chr(9))),
            "untrusted mount output")
    values = line.strip().split()
    require(len(values) == 4, "unexpected findmnt field count")
    source, target, device, fs = values
    require(source in REMOTE and target.startswith("/") and
            bool(re.fullmatch(r"[A-Za-z0-9._/:-]+", device)) and
            bool(re.fullmatch(r"[A-Za-z0-9_-]+", fs)),
            "untrusted or unknown remote path metadata")
    return source, target, device, fs

def parse_remote(text: str) -> dict:
    require(isinstance(text, str) and 0 < len(text) < 4096,
            "remote output missing or unbounded")
    # Validate before splitlines(), which silently strips carriage returns.
    require(not any(control in text for control in (chr(0), chr(13), chr(9))),
            "remote contains control characters")
    lines = text.strip().splitlines()
    require(len(lines) == len(REMOTE), "incomplete remote mount inventory")
    rows = dict()
    for line in lines:
        path, target, source, fs = parse_mount_line(line)
        require(path not in rows, "duplicate path from remote")
        rows[path] = {"mount_target": target,
                      "filesystem_type": fs,
                      "is_exact_mountpoint": target == path}
        # Intentionally avoid preserving raw block device or IP/network export.
    require(set(rows) == set(REMOTE), "remote path set differs from allowlist")
    return rows

def assess(local_target: str, remote: dict) -> dict:
    require(local_target.startswith("/") and isinstance(remote, dict)
            and set(remote) == set(REMOTE), "invalid local or remote evidence")
    mounted = all(remote[key].get("is_exact_mountpoint") is True and
                  remote[key].get("filesystem_type") == expected
                  for key, expected in EXPECTED.items())
    x1_is_fallback = local_target == "/"
    beelink_alias_fallback = (
        remote["/media/scott/NAS5"].get("mount_target") == "/"
        and not remote["/media/scott/NAS5"].get("is_exact_mountpoint"))
    return {
        "decision": "HOLD",
        "x1_nas5_label_mount_target": local_target,
        "x1_nas5_label_is_local_root": x1_is_fallback,
        "beelink_three_expected_devices_mounted": mounted,
        "beelink_nas5_label_is_local_root": beelink_alias_fallback,
        "remote_mounts": remote,
        "production_deployment_authorized": False,
        "production_mounts_modified": False,
        "data_files_read": 0,
        "files_copied_or_mounted": 0,
        "release_blockers": list(BLOCKERS),
    }

def physical():
    require(os.uname().nodename == "x1-370" and os.geteuid() == 1000,
            "x1-370 nonroot only")
    local = subprocess.run(
        ["findmnt", "-T", LOCAL, "-n", "-o", "TARGET"],
        text=True, capture_output=True, timeout=7, check=False)
    require(local.returncode == 0, "x1 mount metadata unavailable")
    target = local.stdout.strip()
    require(target.startswith("/") and chr(10) not in target and len(target) < 200,
            "local findmnt result ambiguous")
    proc = subprocess.run(
        SSH + [BEELINK_ALIAS, REMOTE_SCRIPT],
        text=True, capture_output=True, timeout=14, check=False)
    require(proc.returncode == 0, "trusted Beelink read-only probe unavailable")
    out = assess(target, parse_remote(proc.stdout))
    out.update(host="x1-370",
               mode="strict_host_key_crosshost_readonly_topology",
               remote_alias="beelink",
               ssh_hostkey_explicitly_accepted=False)
    return out

if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-readonly-crosshost", action="store_true")
    parser.add_argument("--json", action="store_true")
    a=parser.parse_args()
    result=(physical() if a.physical_readonly_crosshost else {
        "decision":"HOLD", "physical_executed":False,
        "production_deployment_authorized":False})
    print(json.dumps(result, sort_keys=True, indent=2 if a.json else None))
