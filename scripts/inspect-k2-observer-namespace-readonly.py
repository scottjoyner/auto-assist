#!/usr/bin/env python3
"""Read-only diagnostics for Fleet Commander /proc observer isolation.

Reports only bounded categories. Does not bypass AppArmor, user namespaces,
ptrace, /proc authorization, service policy or canonical admission.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROC = Path("/proc")
PHYSICAL = Path(__file__).with_name("inspect-x1-k2-physical-witness-readonly.py")
KIND = "assistx.k2-observer-namespace-diagnostic.v1"
MAX_METADATA_BYTES = 2048


def physical_runtime_inventory(proc_root: Path) -> dict:
    """Use the existing exact LM Studio/socat CMD match; no new process scan."""
    spec = importlib.util.spec_from_file_location("physical_k2_observer", PHYSICAL)
    if spec is None or spec.loader is None:
        raise ValueError("physical_observer_import_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.process_tree(proc_root)


def _bounded_read(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("proc_metadata_oversized")
    return raw.decode("ascii").strip()


def uid_map(path: Path) -> tuple[tuple[int, int, int], ...]:
    """Parse the published mapping only; don't return raw map in report."""
    rows = _bounded_read(path).splitlines()
    if not 0 < len(rows) <= 8:
        raise ValueError("proc_userns_mapping_invalid")
    parsed = []
    for line in rows:
        words = line.split()
        if len(words) != 3 or any(not item.isdecimal() for item in words):
            raise ValueError("proc_userns_mapping_invalid")
        ids = tuple(map(int, words))
        if ids[2] < 1 or any(num > (1 << 32) for num in ids):
            raise ValueError("proc_userns_mapping_invalid")
        parsed.append(ids)
    return tuple(parsed)


def uid_from_status(path: Path) -> int:
    rows = _bounded_read(path).splitlines()
    values = [line[4:].strip() for line in rows if line.startswith("Uid:")]
    if len(values) != 1 or len(values[0].split()) != 4:
        raise ValueError("proc_uid_unavailable")
    parts = values[0].split()
    if any(not n.isdecimal() for n in parts):
        raise ValueError("proc_uid_unavailable")
    return int(parts[0])


def apparmor_kind(path: Path) -> str:
    """No raw profile names, labels or policy details in output."""
    raw = _bounded_read(path)
    if not raw:
        return "UNKNOWN"
    if raw == "unconfined" or raw.endswith("(unconfined)"):
        return "UNCONFINED_REPORTED"
    if raw.endswith("(enforce)"):
        return "ENFORCED_REPORTED"
    if raw.endswith("(complain)"):
        return "COMPLAIN_REPORTED"
    return "OTHER_OR_UNAVAILABLE"


def _link_status(path: Path, *, reader: Callable[[Path], str]) -> str:
    try:
        reader(path)
        return "READABLE"
    except (OSError, ValueError):
        return "DENIED_OR_UNAVAILABLE"


def collect(
    *, proc_root: Path = PROC, inventory_reader: Callable[[Path], dict] | None = None,
    link_reader: Callable[[Path], str] = os.readlink,
    observer_real_uid: int | None = None,
) -> dict[str, Any]:
    """No raw /proc content or PID is disclosed. Does not prove denial cause."""
    output: dict[str, Any] = {
        "schema": KIND, "node": "x1-370", "collector": "fleet-commander",
        "observed_utc": datetime.now(UTC).isoformat(),
        "observer_user_namespace_map_observed": False,
        "runtime_user_namespace_map_observed": False,
        "observer_target_userns_maps_differ": None,
        "observer_uid_equal_runtime_uid": None,
        "observer_apparmor_mode": "UNKNOWN",
        "runtime_apparmor_mode": "UNKNOWN",
        "observer_executable_link": "DENIED_OR_UNAVAILABLE",
        "runtime_executable_link": "DENIED_OR_UNAVAILABLE",
        "namespace_divergence_observed": False,
        "root_cause_proven": False,
        "physical_process_socket_chain_verified": False,
        "actual_model_residency_verified": False,
        "independent_custody_verified": False,
        "canonical_operator_approval_verified": False,
        "can_dispatch": False, "decision": "HOLD",
        "collection_status": "FAIL_CLOSED",
    }
    try:
        inventory = (
            physical_runtime_inventory(proc_root)
            if inventory_reader is None else inventory_reader(proc_root)
        )
        pid = inventory["root_pid"]
        if type(pid) is not int or pid < 1:
            raise ValueError("untrusted_runtime_pid")
        observer = proc_root / "self"
        target = proc_root / str(pid)
        output["observer_executable_link"] = _link_status(
            observer / "exe", reader=link_reader
        )
        output["runtime_executable_link"] = _link_status(
            target / "exe", reader=link_reader
        )
        local = uid_map(observer / "uid_map")
        output["observer_user_namespace_map_observed"] = True
        remote = uid_map(target / "uid_map")
        output["runtime_user_namespace_map_observed"] = True
        output["observer_target_userns_maps_differ"] = local != remote
        output["namespace_divergence_observed"] = local != remote
        reader_uid = os.getuid() if observer_real_uid is None else observer_real_uid
        if type(reader_uid) is not int or reader_uid < 0:
            raise ValueError("invalid_reader_uid")
        output["observer_uid_equal_runtime_uid"] = (
            reader_uid == uid_from_status(target / "status")
        )
        output["observer_apparmor_mode"] = apparmor_kind(
            observer / "attr/current"
        )
        output["runtime_apparmor_mode"] = apparmor_kind(
            target / "attr/current"
        )
        output["collection_status"] = (
            "ISOLATION_CONTEXT_OBSERVED_NO_HOST_ATTESTATION"
            if local != remote else "SAME_MAPPING_UNPROVEN_CUSTODY"
        )
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    result = collect()
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    # Always fail closed, even with complete security-context metadata.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
