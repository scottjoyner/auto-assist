#!/usr/bin/env python3
"""Read-only x1 NAS5 route reconciliation. Never grants Hermes deployment."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path("/")
CANONICAL = Path("/nas")
LEGACY = Path("/media/scott/NAS5")
EXPECTED_CIFS_SOURCE = "//192.168.1.202/fileserver"
EXPECTED_LIVE_CONTAINER = "assistx-hermes-adapter"
MAX_OUTPUT = 5000

def classify(mountinfo: object, *, canonical_is_mount: bool,
             legacy_is_mount: bool, root_dev: int, canonical_dev: int,
             legacy_dev: int, legacy_bind_present: bool) -> dict:
    if (not isinstance(mountinfo, str) or len(mountinfo) > MAX_OUTPUT
            or any(type(x) is not bool for x in
                   (canonical_is_mount, legacy_is_mount, legacy_bind_present))
            or any(type(x) is not int or x < 0 for x in
                   (root_dev, canonical_dev, legacy_dev))):
        raise ValueError("invalid mount topology input")
    # findmnt -knr -M /nas -o TARGET,FSTYPE,SOURCE. Require exactly the
    # expected CIFS source; autofs alone is never sufficient.
    lines = [line.split() for line in mountinfo.splitlines()]
    sources = [(a[0], a[1], a[2]) for a in lines if len(a) == 3]
    expected = (str(CANONICAL), "cifs", EXPECTED_CIFS_SOURCE)
    has_cifs = sources.count(expected) == 1
    unexpected_cifs = any(fs == "cifs" and (target, fs, source) != expected
                          for target, fs, source in sources)
    route_matches = (canonical_is_mount and canonical_dev != root_dev
                     and has_cifs and not unexpected_cifs)
    legacy_fallback = not legacy_is_mount and legacy_dev == root_dev
    mismatch = route_matches and legacy_fallback and legacy_bind_present
    failures = []
    if not route_matches:
        failures.append("canonical_nas_route_unverified")
    if legacy_fallback:
        failures.append("legacy_nas5_path_is_host_root_directory")
    if legacy_bind_present:
        failures.append("hermes_live_legacy_nas5_bind")
    if mismatch:
        failures.append("legacy_bind_misses_verified_cifs_route")
    if legacy_is_mount:
        failures.append("legacy_path_mount_identity_not_authorized")
    return {
        "schema": 1,
        "mode": "hermes_readonly_nas5_route_reconciliation",
        "canonical_nas_cifs_matches_fleet_inventory": route_matches,
        "legacy_nas5_exact_mountpoint": legacy_is_mount,
        "legacy_nas5_uses_host_root_filesystem": legacy_fallback,
        "running_hermes_has_legacy_nas5_bind": legacy_bind_present,
        "legacy_bind_misses_canonical_cifs": mismatch,
        "remote_share_custody_independently_attested": False,
        "production_knowledge_mount_authorized": False,
        "production_containers_modified": False,
        "production_deployment_authorized": False,
        "decision": "HOLD",
        "failures": sorted(failures),
    }

def _get_readonly_output(argv: list[str], timeout: int = 12,
                         max_output: int = MAX_OUTPUT) -> str:
    run = subprocess.run(argv, capture_output=True, text=True,
                         check=False, timeout=timeout)
    if run.returncode or len(run.stdout) > max_output:
        raise ValueError("metadata inspection unavailable")
    return run.stdout

def physical_readonly() -> dict:
    if os.uname().nodename != "x1-370" or os.getuid() == 0:
        raise ValueError("nonroot x1-370 witness only")
    for path in (ROOT, CANONICAL, LEGACY):
        if not path.is_dir():
            raise ValueError("expected path absent")
    mount_lines = _get_readonly_output(
        ["findmnt", "-knr", "-M", str(CANONICAL),
         "-o", "TARGET,FSTYPE,SOURCE"])
    raw = _get_readonly_output(
        ["docker", "inspect", "--type", "container",
         EXPECTED_LIVE_CONTAINER], timeout=14, max_output=250000)
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("unexpected live Docker metadata")
    mounts = data[0].get("Mounts")
    if not isinstance(mounts, list):
        raise ValueError("Docker mounts unavailable")
    legacy_bind = any(
        isinstance(m, dict)
        and m.get("Type") == "bind"
        and m.get("Destination") == str(LEGACY)
        and m.get("Source") == str(LEGACY)
        for m in mounts)
    return classify(
        mount_lines, canonical_is_mount=CANONICAL.is_mount(),
        legacy_is_mount=LEGACY.is_mount(), root_dev=ROOT.stat().st_dev,
        canonical_dev=CANONICAL.stat().st_dev,
        legacy_dev=LEGACY.stat().st_dev,
        legacy_bind_present=legacy_bind,
    )

def default_hold() -> dict:
    return {
        "schema": 1, "mode": "hermes_readonly_nas5_route_reconciliation",
        "physical_metadata_executed": False, "decision": "HOLD",
        "production_deployment_authorized": False,
        "production_containers_modified": False,
        "failures": ["read_only_inspection_not_requested"],
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-readonly", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    record = default_hold()
    if args.physical_readonly:
        try:
            record = physical_readonly()
            record["physical_metadata_executed"] = True
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired,
                json.JSONDecodeError):
            record["failures"] = ["physical_route_inspection_failed_closed"]
    print(json.dumps(record, sort_keys=True,
                     indent=2 if args.json else None))
    if args.physical_readonly and not record.get("physical_metadata_executed"):
        raise SystemExit(2)

if __name__ == "__main__":
    main()
