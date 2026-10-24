#!/usr/bin/env python3
"""Read-only live Hermes mount inventory + exact synthetic restricted-Compose preflight.

The process reads Docker metadata, not source/file contents. It NEVER starts,
recreates, stops, modifies, or attaches to production. Always returns HOLD.
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import re
import subprocess
import tempfile

import hermes_split_root_normal_compose_canary as candidate

CONTAINER = "assistx-hermes-adapter"
IMAGE = candidate.IMAGE
SSD_ROOT = "/media/scott/SSD_4TB"
PROTECTED = SSD_ROOT + "/knowledge"
NAS_ROOT = "/media/scott/NAS5"
REPO_ROOT = "/home/scott/git"
SSH_ROOT = "/root/.ssh"
EXPECTED_BAN = (
    "production_broad_rw_storage_mounts_unremediated",
    "production_mutable_application_and_repo_mounts_unremediated",
    "production_private_ssh_mounts_must_be_removed",
    "independent_source_signature_review_missing",
    "owner_authorized_compatibility_and_revocation_unverified",
    "production_rollback_unverified",
)

class UnsafeAudit(ValueError):
    pass

def need(value: bool, message: str):
    if not value:
        raise UnsafeAudit(message)

def is_inside(parent: str, child: str) -> bool:
    return os.path.commonpath((os.path.abspath(parent), os.path.abspath(child))) == os.path.abspath(parent)

def accessible_ancestor(path: str, protected: str) -> bool:
    """Writable bind exposes protected parent itself or contains protected tree."""
    return is_inside(path, protected)

def classify(mount: dict, resolver=os.path.realpath) -> dict:
    need(isinstance(mount, dict), "malformed mount")
    source = mount.get("Source")
    dest = mount.get("Destination")
    writable = mount.get("RW")
    need(isinstance(source, str) and source.startswith("/")
         and isinstance(dest, str) and dest.startswith("/")
         and type(writable) is bool, "unsafe/unrecognized mount metadata")
    resolved = resolver(source)
    need(isinstance(resolved, str) and resolved.startswith("/"), "bad source resolution")
    risks = []
    if writable and accessible_ancestor(resolved, PROTECTED):
        risks.append("writable_protected_corpus_or_ancestor")
    if writable and accessible_ancestor(resolved, SSD_ROOT):
        risks.append("writable_entire_ssd")
    if writable and (resolved == NAS_ROOT or accessible_ancestor(resolved, NAS_ROOT)):
        risks.append("writable_nas_root_or_ancestor")
    if writable and (is_inside(REPO_ROOT, source) or is_inside(REPO_ROOT, resolved)):
        risks.append("mutable_repository_mount")
    if writable and dest == "/app/src":
        risks.append("mutable_runtime_application_source")
    if dest == "/root/knowledge" and writable:
        risks.append("legacy_knowledge_root_writable")
    if dest.startswith(SSH_ROOT + "/") or dest == SSH_ROOT:
        risks.append("private_ssh_mount_not_allowed_in_candidate")
    if source != resolved:
        risks.append("source_is_symlink_or_alias")
    if writable and not risks:
        risks.append("unreviewed_writable_mount")
    return {
        "destination": dest,
        "source": source,
        "source_realpath": resolved,
        "writable": writable,
        "risks": sorted(set(risks)),
    }

def inspect_production(doc: dict) -> dict:
    need(isinstance(doc, dict), "invalid production metadata")
    mounts = doc.get("Mounts")
    state = doc.get("State", {})
    if not isinstance(state, dict):
        raise UnsafeAudit("production metadata missing state")
    need(isinstance(mounts, list) and len(mounts) < 100, "missing/bad mounts")
    observed = [classify(m) for m in mounts]
    return {
        "running": state.get("Running") is True,
        "image": doc.get("Image"),
        "mount_count": len(observed),
        "writable_mount_count": sum(1 for m in observed if m["writable"]),
        "risky_mount_count": sum(1 for m in observed if m["risks"]),
        "mounts": observed,
        "environment_inspected": False,
        "secret_values_exposed": False,
        "protected_corpus_writable_aliases": [
            m["destination"] for m in observed
            if "writable_protected_corpus_or_ancestor" in m["risks"]
        ],
        "nas_root_rw_present": any(
            "writable_nas_root_or_ancestor" in m["risks"] for m in observed
        ),
        "runtime_src_rw_present": any(
            "mutable_runtime_application_source" in m["risks"] for m in observed
        ),
    }

def validate_candidate(doc: dict, image: str, read: pathlib.Path, write: pathlib.Path) -> bool:
    if (not isinstance(doc, dict) or not isinstance(image, str)
        or not re.fullmatch("sha256:[a-f0-9]{64}", image)):
        return False
    if not (read.is_absolute() and write.is_absolute() and read != write
            and read.parent == write.parent and read.resolve() == read
            and write.resolve() == write and not read.is_symlink()
            and not write.is_symlink()):
        return False
    try:
        return candidate.restricted_render(doc, image, read, write)
    except (KeyError, TypeError, ValueError, OSError):
        return False

def read_only_report() -> dict:
    """Read only source/destination/mode metadata from Docker; no env or secrets."""
    need(os.uname().nodename == "x1-370" and os.geteuid() == 1000,
         "non-root x1-370 audit only")
    mounts = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", CONTAINER],
        capture_output=True, text=True, timeout=12, check=False,
    )
    status = subprocess.run(
        ["docker", "inspect", "--format", "{{.Image}} {{.State.Running}}", CONTAINER],
        capture_output=True, text=True, timeout=12, check=False,
    )
    need(mounts.returncode == 0 and status.returncode == 0
         and len(mounts.stdout) < 150000, "live inspect failed")
    image, running = status.stdout.strip().split()
    production = inspect_production({
        "Mounts": json.loads(mounts.stdout),
        "Image": image, "State": {"Running": running == "true"},
    })
    need(production["running"] and production["image"] == IMAGE,
         "production Hermes image or running status changed")
    with tempfile.TemporaryDirectory(
        prefix="og-hermes-containment-", dir="/home/scott/.cache",
    ) as tmp:
        root = pathlib.Path(tmp)
        ro, rw = root / "knowledge", root / "worktree"
        ro.mkdir()
        rw.mkdir()
        synthetic_image = "sha256:" + "b" * 64
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(pathlib.Path.home()),
            "HERMES_CANDIDATE_IMAGE": synthetic_image,
            "HERMES_SYNTHETIC_KNOWLEDGE": str(ro),
            "HERMES_SYNTHETIC_WORKSPACE": str(rw),
        }
        rendered = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null",
             "-f", str(candidate.COMPOSE), "--profile", "synthetic-canary",
             "config", "--format", "json"],
            env=env, text=True, capture_output=True, timeout=20, check=False,
        )
        need(rendered.returncode == 0 and len(rendered.stdout) < 40000,
             "synthetic Compose render failed")
        verified = validate_candidate(json.loads(rendered.stdout),
                                      synthetic_image, ro, rw)
    need(verified, "synthetic Compose is not restricted")
    return {
        "schema": 1,
        "decision": "HOLD",
        "mode": "production_readonly_mount_metadata_audit",
        "production_modified": False,
        "production_deployment_authorized": False,
        "real_agent_tasks_executed": 0,
        "provider_requests_forwarded": 0,
        "synthetic_candidate_compose_verified": True,
        "candidate_mount_destinations": ["/research/knowledge", "/workspace"],
        "production": production,
        "release_blockers": list(EXPECTED_BAN),
    }

def default_report() -> dict:
    return {
        "decision": "HOLD",
        "mode": "production_readonly_mount_metadata_audit",
        "production_modified": False,
        "production_audit_executed": False,
        "production_deployment_authorized": False,
    }

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-production-readonly", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = read_only_report() if args.audit_production_readonly else default_report()
    print(json.dumps(report, sort_keys=True, indent=2 if args.json else None))
