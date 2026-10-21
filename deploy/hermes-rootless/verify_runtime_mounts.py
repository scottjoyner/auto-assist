#!/usr/bin/env python3
"""Read-only, fail-closed Hermes Docker runtime/mount witness.

A PASS qualifies *only* a specified synthetic container, never production.
Default: no Docker access. Live audit: Docker inspect metadata only; no writes.
Never prints mount sources, Docker environment variables, or credential data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

CONTAINER = "assistx-hermes-adapter"
IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_MOUNTS = {
    "/research/knowledge": False,
    "/workspace": True,
}
VIOLATIONS = (
    "missing_approved_sources", "missing_pinned_image", "incorrect_image",
    "wrong_user", "unexpected_entrypoint", "network_exposed", "rootfs_writable",
    "capabilities_present", "privilege_escalation_possible",
    "host_namespace_or_device", "resources_unbounded",
    "unexpected_ports", "mount_scope_violation",
)

def _source_ok(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("/"):
        return False
    p = Path(value)
    try:
        # An alias, symlinked parent, or absent path is not an approved bind.
        return (p.is_dir() and not p.is_symlink()
                and str(p.resolve(strict=True)) == value
                and not any(x in ("..", ".") for x in p.parts))
    except (OSError, RuntimeError, ValueError):
        return False

def audit(record: object, *, readonly_source: str | None = None,
          private_source: str | None = None,
          image_id: str | None = None) -> dict:
    """Evaluate docker inspect JSON. Input is NOT logged or written to disk."""
    errors: set[str] = set()
    if not isinstance(record, dict):
        record = {}
    host = record.get("HostConfig")
    conf = record.get("Config")
    mounts = record.get("Mounts")
    host = host if isinstance(host, dict) else {}
    conf = conf if isinstance(conf, dict) else {}

    if not (_source_ok(readonly_source) and _source_ok(private_source)
            and readonly_source != private_source):
        errors.add("missing_approved_sources")
    if not isinstance(image_id, str) or not IMAGE.fullmatch(image_id):
        errors.add("missing_pinned_image")
    elif record.get("Image") != image_id:
        errors.add("incorrect_image")
    if conf.get("User") != "1000:1000":
        errors.add("wrong_user")
    if conf.get("Entrypoint") != ["/entrypoint.sh"]:
        errors.add("unexpected_entrypoint")
    if host.get("NetworkMode") != "none":
        errors.add("network_exposed")
    if host.get("ReadonlyRootfs") is not True:
        errors.add("rootfs_writable")
    drops = host.get("CapDrop")
    adds = host.get("CapAdd") or []
    if not isinstance(drops, list) or [x.lower() for x in drops] != ["all"] or adds:
        errors.add("capabilities_present")
    security = host.get("SecurityOpt") or []
    if (host.get("Privileged") is not False
            or not isinstance(security, list)
            or not any(s in ("no-new-privileges", "no-new-privileges:true")
                       for s in security)):
        errors.add("privilege_escalation_possible")
    if (host.get("PidMode") not in ("", None)
            or host.get("IpcMode") not in ("private", "", None)
            or host.get("Devices") or host.get("Binds")  # Compose uses mounts, not --volume strings
            or host.get("NetworkMode") == "host"):
        errors.add("host_namespace_or_device")
    if (host.get("PidsLimit") not in (32,)
            or host.get("Memory") not in (402653184,)
            or host.get("NanoCpus") not in (500000000,)):
        errors.add("resources_unbounded")
    # EXPOSE in inherited image metadata does NOT publish a port.
    # NetworkMode=none and actual HostConfig.PortBindings must still be enforced.
    if host.get("PortBindings"):
        errors.add("unexpected_ports")

    if not isinstance(mounts, list) or len(mounts) != 2:
        errors.add("mount_scope_violation")
    else:
        found: set[str] = set()
        expected = {"/research/knowledge": readonly_source,
                    "/workspace": private_source}
        for item in mounts:
            if not isinstance(item, dict):
                errors.add("mount_scope_violation")
                continue
            dest = item.get("Destination")
            source = item.get("Source")
            if (dest not in REQUIRED_MOUNTS or dest in found
                    or item.get("Type") != "bind"
                    or item.get("RW") is not REQUIRED_MOUNTS.get(dest)
                    or source != expected.get(dest)
                    or not _source_ok(source)):
                errors.add("mount_scope_violation")
            found.add(dest)
        if found != set(REQUIRED_MOUNTS):
            errors.add("mount_scope_violation")

    return {
        "schema": 1,
        "mode": "hermes_readonly_runtime_mount_witness",
        "synthetic_runtime_policy_pass": not errors,
        "decision": "HOLD",
        "production_deployment_authorized": False,
        "production_containers_modified": False,
        "provider_requests_forwarded": 0,
        "mount_count_observed": len(mounts) if isinstance(mounts, list) else None,
        "writable_mount_count": (sum(x.get("RW") is True for x in mounts
                                     if isinstance(x, dict))
                                 if isinstance(mounts, list) else None),
        "violations": sorted(errors),
    }

def live_audit() -> dict:
    # The running legacy worker can NEVER pass because it is not an
    # approved synthetic fixture. No production mount path is output.
    response = subprocess.run(
        ["docker", "inspect", "--type", "container", CONTAINER],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if response.returncode or len(response.stdout) > 250000:
        raise ValueError("Docker inspect unavailable or oversized")
    records = json.loads(response.stdout)
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError("unexpected Docker inspect response")
    return audit(records[0])

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--audit-live-readonly", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.audit_live_readonly:
        if os.uname().nodename != "x1-370" or os.getuid() == 0:
            raise SystemExit("live inspection requires unprivileged x1-370 operator")
        try:
            data = live_audit()
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired,
                json.JSONDecodeError):
            data = audit(None)
            data["violations"].append("live_inspection_failed_closed")
            data["synthetic_runtime_policy_pass"] = False
    else:
        data = audit(None)
    print(json.dumps(data, sort_keys=True, indent=2 if args.json else None))
    if args.audit_live_readonly and data["synthetic_runtime_policy_pass"]:
        raise SystemExit("unexpected live runtime policy approval")

if __name__ == "__main__":
    main()
