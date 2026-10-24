#!/usr/bin/env python3
"""Host-only read-only mount provenance and scratch-capacity qualification.

This never approves a real corpus mount, keys, or a production deployment.
It reads directory metadata and findmnt output only, never file content.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess

SCRATCH = Path("/home/scott/.cache")
SSD = Path("/media/scott/SSD_4TB")
CORPUS_ALIAS = Path("/home/scott/knowledge")
NAS_LABEL = Path("/media/scott/NAS5")
GIT = Path("/home/scott/git")
EXPECTED_NODES = {
    "scratch": SCRATCH,
    "ssd": SSD,
    "corpus_alias": CORPUS_ALIAS,
    "nas5_label": NAS_LABEL,
    "git_root": GIT,
}
READONLY_COMMAND = "findmnt"
MIN_CAPACITY_BYTES = 20 * 1024**3
RELEASE_BLOCKERS = (
    "production_writable_ancestor_mounts_still_present",
    "corpus_source_not_authorized_or_independently_signed",
    "production_scratch_not_reserved_or_independently_approved",
    "owner_task_grants_fencing_and_revocation_unverified",
    "rollback_and_independent_reviewer_unverified",
)

class UnsafeTopology(ValueError):
    pass

def need(test, message):
    if not test:
        raise UnsafeTopology(message)

def within(parent: str, child: str) -> bool:
    return os.path.commonpath((parent, child)) == parent

def analyze_path(path: Path) -> dict:
    """No traversal into path, inspection of contents or Docker interaction."""
    need(path in EXPECTED_NODES.values() and path.is_absolute(),
         "unapproved source path")
    need(path.exists() and path.is_dir(), "required host path unavailable")
    resolved = os.path.realpath(path)
    need(os.path.isabs(resolved), "failed to canonicalize source path")
    result = subprocess.run(
        [READONLY_COMMAND, "--target", str(path), "--noheadings",
         "--output", "TARGET,SOURCE,FSTYPE"],
        capture_output=True, text=True, timeout=7, check=False,
    )
    need(result.returncode == 0, "cannot verify mount topology")
    lines = result.stdout.strip().splitlines()
    need(len(lines) == 1, "ambiguous mounted filesystem")
    parts = lines[0].split(maxsplit=2)
    need(len(parts) == 3 and parts[0].startswith("/") and parts[1]
         and parts[2], "invalid mount topology result")
    st = os.stat(path)
    vfs = os.statvfs(path)
    # No storage is allocated, written, mounted or reserved.
    return {
        "host_path": str(path),
        "resolved_path": resolved,
        "is_symlink": path.is_symlink(),
        "is_mountpoint": os.path.ismount(path),
        "mount_target": parts[0],
        "filesystem_type": parts[2],
        "device_id": st.st_dev,
        "directory_owner_uid": st.st_uid,
        "directory_mode": st.st_mode & 0o777,
        "available_bytes_now": vfs.f_bavail * vfs.f_frsize,
    }

def assess_topology(paths: dict[str, dict]) -> dict:
    need(set(paths) == set(EXPECTED_NODES), "incomplete host path inventory")
    for key, expected in EXPECTED_NODES.items():
        item = paths[key]
        need(isinstance(item, dict) and item.get("host_path") == str(expected)
             and isinstance(item.get("resolved_path"), str)
             and isinstance(item.get("device_id"), int)
             and type(item.get("is_mountpoint")) is bool
             and type(item.get("is_symlink")) is bool
             and isinstance(item.get("available_bytes_now"), int)
             and isinstance(item.get("directory_mode"), int),
             "invalid host path evidence: " + key)
    ssd, scratch, alias, nas, git = (
        paths[name] for name in
        ("ssd", "scratch", "corpus_alias", "nas5_label", "git_root")
    )
    flags = {
        "ssd_is_separate_mount": (
            ssd["is_mountpoint"] and ssd["mount_target"] == str(SSD)),
        "corpus_alias_resolves_inside_ssd": (
            alias["is_symlink"] and within(ssd["resolved_path"],
                                           alias["resolved_path"])),
        "scratch_outside_protected_ssd": (
            not scratch["is_symlink"] and
            not within(ssd["resolved_path"], scratch["resolved_path"]) and
            scratch["device_id"] != ssd["device_id"]),
        "scratch_owner_private": (
            scratch["directory_owner_uid"] == 1000 and
            scratch["directory_mode"] & 0o077 == 0),
        "scratch_has_20g_current_headroom": (
            scratch["available_bytes_now"] >= MIN_CAPACITY_BYTES),
        "nas5_label_is_actual_mountpoint": (
            nas["is_mountpoint"] and nas["mount_target"] == str(NAS_LABEL)),
        "nas5_label_shares_local_root_device": (
            nas["device_id"] == scratch["device_id"]),
        "scratch_and_git_share_device": (
            scratch["device_id"] == git["device_id"]),
    }
    blockers = list(RELEASE_BLOCKERS)
    if not flags["ssd_is_separate_mount"]:
        blockers.append("expected_ssd_mount_missing")
    if not flags["corpus_alias_resolves_inside_ssd"]:
        blockers.append("corpus_alias_changed_or_unverified")
    if not flags["scratch_outside_protected_ssd"]:
        blockers.append("scratch_is_not_separate_from_protected_ssd")
    if not flags["scratch_owner_private"]:
        blockers.append("scratch_parent_owner_or_permissions_unsafe")
    if not flags["scratch_has_20g_current_headroom"]:
        blockers.append("scratch_current_space_below_minimum")
    if not flags["nas5_label_is_actual_mountpoint"]:
        blockers.append("nas5_label_is_not_a_mountpoint_on_this_host")
    if flags["nas5_label_shares_local_root_device"]:
        blockers.append("nas5_label_is_local_root_filesystem_not_verified_nas")
    if flags["scratch_and_git_share_device"]:
        blockers.append("scratch_shares_local_device_with_mutable_repositories")
    return {
        "decision": "HOLD",
        "production_deployment_authorized": False,
        "scratch_capacity_reserved": False,
        "input_source_approved": False,
        "nas5_availability_claimed": False,
        "filesystem_topology_flags": flags,
        "release_blockers": blockers,
        "paths": paths,
    }

def physical_readonly():
    need(os.uname().nodename == "x1-370" and os.geteuid() == 1000,
         "topology witness restricted to x1-370 nonroot user")
    paths = {k: analyze_path(path) for k, path in EXPECTED_NODES.items()}
    report = assess_topology(paths)
    report.update(host="x1-370",
                  mode="read_only_storage_topology_snapshot",
                  real_files_read=0,
                  protected_corpus_files_opened=0,
                  production_modified=False,
                  docker_operations_executed=0)
    return report

if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--physical-readonly", action="store_true")
    p.add_argument("--json", action="store_true")
    arg=p.parse_args()
    report=(physical_readonly() if arg.physical_readonly else
            {"decision":"HOLD", "physical_executed":False,
             "production_deployment_authorized":False})
    print(json.dumps(report, sort_keys=True, indent=2 if arg.json else None))
