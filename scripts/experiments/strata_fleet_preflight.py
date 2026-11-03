#!/usr/bin/env python3
"""Read-only Strata hardware and non-disruptive trial preflight.

Read PCI vendor/device IDs, /proc/meminfo, /proc/*/comm and disk availability.
No SSH, network, model downloads, subprocess execution, service changes,
driver installation, inference or authorization to deploy is implemented.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import socket
import stat
import sys
import os

STRATA_RELEASE = "v0.2.10"  # observed GitHub release Nov 2, 2026
# Narrow, source-verified mappings ONLY. Never guess a GPU from AMD vendor alone.
KNOWN_GPUS = {
    ("0x1002", "0x7551"): ("gfx1201", "Radeon AI PRO R9700", "rx9070", "supported"),
    ("0x1002", "0x1586"): ("gfx1151", "Ryzen AI Max / Strix Halo", "halo", "experimental"),
    ("0x1002", "0x150e"): ("gfx1150", "Radeon 890M / Strix Point", None, "unsupported"),
}
INFERENCE_PROCESS_NAMES = frozenset({
    "llama-server", "llama.cpp", "ollama", "vllm", "vllm serve",
    "strata", "strata-engine", "strata-dsv41", "llama-cli", "lms",
})
DEFAULT_MIN_AVAILABLE_RAM_MIB = 8192
DEFAULT_MIN_ROOT_FREE_GIB = 40
MAX_DEVICES = 24
MAX_PROCESSES = 8192


def parse_memavailable_mib(text: str) -> int | None:
    matches = re.findall(r"(?m)^MemAvailable:\s*(\d+)\s+kB\s*$", text)
    return int(matches[0]) // 1024 if len(matches) == 1 else None


def pci_profile(vendor: str, device: str) -> dict:
    vendor, device = vendor.strip().lower(), device.strip().lower()
    profile = KNOWN_GPUS.get((vendor, device))
    if profile:
        gfx, label, variant, support = profile
    else:
        gfx, label, variant, support = None, "Unmapped GPU (not assumed supported)", None, "unknown"
    return {
        "pci_vendor": vendor if re.fullmatch(r"0x[0-9a-f]{4}", vendor) else "unknown",
        "pci_device": device if re.fullmatch(r"0x[0-9a-f]{4}", device) else "unknown",
        "gfx_target": gfx, "device_family": label, "strata_variant": variant,
        "hardware_support": support,
    }


def assess(snapshot: dict, *, ram_floor_mib: int = DEFAULT_MIN_AVAILABLE_RAM_MIB,
           root_floor_gib: int = DEFAULT_MIN_ROOT_FREE_GIB) -> dict:
    if type(snapshot) is not dict or type(ram_floor_mib) is not int or not 1024 <= ram_floor_mib <= 65536:
        raise ValueError("invalid snapshot or RAM floor")
    if type(root_floor_gib) is not int or not 1 <= root_floor_gib <= 1000:
        raise ValueError("invalid disk floor")
    devices = snapshot.get("devices")
    if type(devices) is not list or len(devices) > MAX_DEVICES:
        raise ValueError("invalid GPU device list")
    if any(type(d) is not dict or d.get("hardware_support") not in ("supported", "experimental", "unsupported", "unknown")
           or d.get("strata_variant") not in (None, "rx9070", "halo")
           or d.get("gfx_target") not in (None, "gfx1201", "gfx1151", "gfx1150")
           for d in devices):
        raise ValueError("invalid device record")
    # Re-resolve from actual device IDs rather than trusting caller-supplied
    # support/variant strings. A forged hardware label cannot grant eligibility.
    normalized = [pci_profile(str(d.get("pci_vendor", "")),str(d.get("pci_device", ""))) for d in devices]
    eligible = [x for x in normalized if x["strata_variant"] is not None]
    eligible.sort(key=lambda d: (d["hardware_support"] == "experimental", d["pci_device"]))
    ram = snapshot.get("mem_available_mib")
    root = snapshot.get("root_available_gib")
    processes = snapshot.get("inference_processes")
    if ram is not None and (type(ram) is not int or ram < 0):
        raise ValueError("invalid free RAM")
    if root is not None and (type(root) not in (int,float) or not 0 <= root <= 1_000_000):
        raise ValueError("invalid disk availability")
    if type(processes) is not list or len(processes) > 64 or any(type(p) is not str or p not in INFERENCE_PROCESS_NAMES for p in processes):
        raise ValueError("invalid process evidence")
    blockers = []
    if not eligible:
        blockers.append("NO_VERIFIED_SUPPORTED_GPU")
    if ram is None or ram < ram_floor_mib:
        blockers.append("INSUFFICIENT_OR_UNKNOWN_AVAILABLE_RAM")
    if root is None or root < root_floor_gib:
        blockers.append("INSUFFICIENT_OR_UNKNOWN_ROOT_STORAGE")
    if processes:
        blockers.append("EXISTING_INFERENCE_PROCESS_ACTIVE")
    state = "RESEARCH_PRECHECK_CLEAR" if not blockers else "HOLD"
    # Even a clear precheck is not deployment authorization.
    return {
        "kind": "strata_non_disruptive_preflight_v1",
        "hostname": str(snapshot.get("hostname","unknown"))[:48],
        "strata_release_reference": STRATA_RELEASE,
        "verified_device_profiles": normalized,
        "candidate_variant": eligible[0]["strata_variant"] if eligible else None,
        "mem_available_mib": ram,
        "root_available_gib": root,
        "active_inference_process_names": sorted(set(processes)),
        "research_reserve_floor_mib": ram_floor_mib,
        "research_reserve_floor_root_gib": root_floor_gib,
        "blockers": blockers,
        "precheck_state": state,
        "production_admission": "HOLD",
        "model_or_engine_download_allowed": False,
        "service_install_or_restart_allowed": False,
        "driver_change_allowed": False,
        "inference_dispatch_allowed": False,
        "original_service_preservation_required": True,
        "model_compatibility_verified": False,
        "actual_inference_benchmark_executed": False,
        "signed_graph_custody_verified": False,
        "note": "PCI-match plus conservative spare-resource heuristic, not full capability or model-package proof.",
    }


def collect() -> dict:
    devices = []
    for folder in sorted(Path("/sys/class/drm").glob("card[0-9]*/device"))[:MAX_DEVICES]:
        try:
            v = (folder / "vendor").read_text(encoding="ascii")[:32]
            d = (folder / "device").read_text(encoding="ascii")[:32]
            devices.append(pci_profile(v,d))
        except (OSError, UnicodeError):
            devices.append(pci_profile("unknown", "unknown"))
    try:
        ram = parse_memavailable_mib(Path("/proc/meminfo").read_text(encoding="ascii")[:20000])
    except (OSError, UnicodeError):
        ram = None
    try:
        stats = os.statvfs("/")
        root_gib = round(stats.f_bavail * stats.f_frsize / (1024**3), 3)
    except OSError:
        root_gib = None
    names = set()
    try:
        process_paths = list(Path("/proc").glob("[0-9]*/comm"))[:MAX_PROCESSES]
        for p in process_paths:
            try:
                name = p.read_text(encoding="ascii")[:72].strip()
                if name in INFERENCE_PROCESS_NAMES:
                    names.add(name)
            except (OSError, UnicodeError):
                continue
    except OSError:
        names = set()
    # Without complete process enumeration, we cannot prove no servers.
    return {
        "hostname": socket.gethostname().split(".")[0],
        "devices": devices, "mem_available_mib": ram,
        "root_available_gib": root_gib,
        "inference_processes": sorted(names),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ram-reserve-mib",type=int,default=DEFAULT_MIN_AVAILABLE_RAM_MIB)
    parser.add_argument("--root-reserve-gib",type=int,default=DEFAULT_MIN_ROOT_FREE_GIB)
    args = parser.parse_args(argv)
    try:
        report = assess(collect(),ram_floor_mib=args.ram_reserve_mib,root_floor_gib=args.root_reserve_gib)
    except (OSError, ValueError, TypeError):
        print("Strata preflight: incomplete or invalid system evidence",file=sys.stderr)
        return 2
    print(json.dumps(report,sort_keys=True,allow_nan=False))
    return 0 if report["precheck_state"] == "RESEARCH_PRECHECK_CLEAR" else 3


if __name__ == "__main__":
    raise SystemExit(main())
