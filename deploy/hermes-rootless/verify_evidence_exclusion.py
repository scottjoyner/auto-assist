#!/usr/bin/env python3
"""Read-only Hermes Watchdog evidence mount exclusion witness.

No corpus reads, no Docker modifications, no release authorization.
Unknown host sources or invalid inspect records fail closed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

PROTECTED_EVIDENCE = (
    "/media/scott/SSD_4TB/knowledge/50-Research/model-evaluation/free-first-monitor",
)
LIVE_CONTAINER = "assistx-hermes-adapter"
MAX_INSPECT_BYTES = 250_000


def _normalized_existing(path: object) -> Path:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("invalid_source")
    raw = Path(path)
    if ".." in raw.parts:
        raise ValueError("parent_traversal")
    # Resolve existing symlinks before checking ancestor/descendant scope.
    try:
        resolved = raw.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("source_unavailable") from exc
    if not resolved.is_dir():
        raise ValueError("source_not_directory")
    return resolved


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def audit(record: object, *, protected_roots: tuple[str, ...] = PROTECTED_EVIDENCE) -> dict:
    """Classify mount overlap using filesystem metadata, never file contents.

    protected_roots is injectable only for synthetic unit tests. The physical
    CLI always uses the fixed host protection boundary above.
    """
    failures: set[str] = set()
    exposed = 0
    writable_exposed = 0
    readable = 0
    unknown = 0
    roots: list[Path] = []
    if not isinstance(protected_roots, tuple) or not protected_roots:
        failures.add("protected_roots_untrusted")
    else:
        for raw in protected_roots:
            try:
                roots.append(_normalized_existing(raw))
            except ValueError:
                failures.add("protected_root_unavailable")

    mounts = record.get("Mounts") if isinstance(record, dict) else None
    if not isinstance(mounts, list):
        failures.add("mount_inventory_unavailable")
        mounts = []
    elif len(mounts) > 128:
        failures.add("mount_inventory_oversized")

    if not mounts:
        # An empty synthetic record is not independent runtime evidence.
        failures.add("mount_inventory_empty")
    targets: set[str] = set()
    for mount in mounts[:129]:
        if not isinstance(mount, dict):
            failures.add("invalid_mount_record")
            unknown += 1
            continue
        dest = mount.get("Destination")
        source = mount.get("Source")
        kind = mount.get("Type")
        rw = mount.get("RW")
        if (not isinstance(dest, str) or not dest.startswith("/")
                or dest in targets or kind != "bind" or type(rw) is not bool):
            failures.add("mount_provenance_invalid")
            unknown += 1
            continue
        targets.add(dest)
        try:
            canonical = _normalized_existing(source)
        except ValueError:
            failures.add("mount_source_unavailable")
            unknown += 1
            continue
        if any(_overlap(canonical, root) for root in roots):
            exposed += 1
            if rw:
                writable_exposed += 1
            else:
                readable += 1
    if exposed:
        failures.add("protected_evidence_exposed_by_bind")
    if writable_exposed:
        failures.add("protected_evidence_writable_alias")
    if readable:
        failures.add("protected_evidence_readable_alias")
    return {
        "schema": 1,
        "mode": "hermes_watchdog_evidence_mount_exclusion",
        "protected_evidence_path_exclusion_pass": not failures,
        "mount_count_observed": len(mounts),
        "protected_overlap_count": exposed,
        "protected_overlap_writable_count": writable_exposed,
        "protected_overlap_readonly_count": readable,
        "unknown_mount_count": unknown,
        "violations": sorted(failures),
        "decision": "HOLD",
        "independent_witness_custody_verified": False,
        "production_deployment_authorized": False,
        "production_containers_modified": False,
        "evidence_files_read_or_modified": False,
    }


def inspect_live_readonly() -> dict:
    if os.uname().nodename != "x1-370" or os.getuid() == 0:
        raise ValueError("nonroot_x1_only")
    proc = subprocess.run(
        ["docker", "inspect", "--type", "container", LIVE_CONTAINER],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if proc.returncode or len(proc.stdout) > MAX_INSPECT_BYTES:
        raise ValueError("inspect_failed")
    records = json.loads(proc.stdout)
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("unexpected_inspect_response")
    return audit(records[0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-live-readonly", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.audit_live_readonly:
        try:
            result = inspect_live_readonly()
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired,
                json.JSONDecodeError):
            result = audit(None)
            result["protected_evidence_path_exclusion_pass"] = False
            result["violations"] = sorted(
                set(result["violations"]) | {"physical_inspection_failed_closed"})
    else:
        # Default is deny-only without looking at the host or Docker daemon.
        result = {
            "schema": 1, "mode": "hermes_watchdog_evidence_mount_exclusion",
            "protected_evidence_path_exclusion_pass": False,
            "decision": "HOLD",
            "violations": ["inspection_not_requested"],
            "production_deployment_authorized": False,
            "evidence_files_read_or_modified": False,
        }
    print(json.dumps(result, indent=2 if args.json else None, sort_keys=True))
    if args.audit_live_readonly and result["protected_evidence_path_exclusion_pass"]:
        # Existing unsafe deployment must not silently become a release PASS.
        raise SystemExit("unexpected_live_evidence_exclusion_pass")

if __name__ == "__main__":
    main()
