#!/usr/bin/env python3
"""Read-only Hermes storage-root mountpoint witness: never reads corpus files.

A pass means the *mount topology* is plausible, not independent custody,
storage authorization, content integrity, or production deployment approval.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path("/")
NAS5 = Path("/media/scott/NAS5")
SSD4 = Path("/media/scott/SSD_4TB")

def verdict(*, nas_mounted: bool, ssd_mounted: bool,
            root_dev: int, nas_dev: int, ssd_dev: int) -> dict:
    if (any(type(x) is not bool for x in (nas_mounted, ssd_mounted))
            or any(type(x) is not int or x < 0
                   for x in (root_dev, nas_dev, ssd_dev))):
        raise ValueError("invalid storage topology record")
    distinct = nas_dev != root_dev and ssd_dev != root_dev
    failures = []
    if not nas_mounted:
        failures.append("nas5_not_exact_mountpoint")
    if not ssd_mounted:
        failures.append("ssd_not_exact_mountpoint")
    if nas_dev == root_dev:
        failures.append("nas5_on_host_root_filesystem")
    if ssd_dev == root_dev:
        failures.append("ssd_on_host_root_filesystem")
    if nas_dev == ssd_dev:
        failures.append("nas5_ssd_share_device")
    return {
        "schema": 1,
        "mode": "hermes_storage_root_metadata_preflight",
        "nas5_exact_mountpoint": nas_mounted,
        "ssd_exact_mountpoint": ssd_mounted,
        "nas5_differs_from_host_root": nas_dev != root_dev,
        "ssd_differs_from_host_root": ssd_dev != root_dev,
        "metadata_topology_gate_pass": bool(
            nas_mounted and ssd_mounted and distinct and nas_dev != ssd_dev),
        "failures": sorted(failures),
        "production_deployment_authorized": False,
        "production_data_read_or_modified": False,
        "decision": "HOLD",
    }

def physical_probe() -> dict:
    # Only directory and mountpoint metadata, never listings/content.
    if os.uname().nodename != "x1-370" or os.getuid() == 0:
        raise ValueError("x1 nonroot witness only")
    root, nas, ssd = (p.stat() for p in (ROOT, NAS5, SSD4))
    return verdict(nas_mounted=NAS5.is_mount(),
                   ssd_mounted=SSD4.is_mount(),
                   root_dev=root.st_dev, nas_dev=nas.st_dev,
                   ssd_dev=ssd.st_dev)

def default_hold() -> dict:
    return {
        "schema": 1, "mode": "hermes_storage_root_metadata_preflight",
        "metadata_topology_gate_pass": False,
        "physical_metadata_probe_executed": False,
        "production_deployment_authorized": False,
        "production_data_read_or_modified": False,
        "decision": "HOLD",
        "failures": ["physical_metadata_probe_not_requested"],
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--physical-metadata-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = default_hold()
    if args.physical_metadata_only:
        try:
            result = physical_probe()
            result["physical_metadata_probe_executed"] = True
        except (OSError, ValueError, TypeError):
            result["failures"] = ["physical_storage_topology_unavailable"]
    print(json.dumps(result, sort_keys=True, indent=2 if args.json else None))
    if args.physical_metadata_only and not result["physical_metadata_probe_executed"]:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
