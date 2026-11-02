#!/usr/bin/env python3
"""Read-only, fail-closed proof for removing ONLY AssistX API's /app/src bind.

This is a release-design preflight, never an installer. It calls only Docker
inspect and Docker Compose config. All resolved environment, mount source paths,
secrets, and subprocess output remain in memory; only Boolean/count receipts
are printed. It does NOT inspect, save, load, stop, start, or replace images.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

CONTAINER = "assistx-api"
SOURCE_TARGET = "/app/src"
MAX_CONFIG_BYTES = 8_000_000


class PreflightHold(Exception):
    pass


def _run(argv: list[str], *, cwd: str | None = None, stdin: str | None = None) -> str:
    try:
        response = subprocess.run(
            argv, cwd=cwd, input=stdin, text=True, capture_output=True,
            check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreflightHold("SUBPROCESS_UNAVAILABLE") from None
    if response.returncode != 0 or len(response.stdout) > MAX_CONFIG_BYTES:
        # NEVER expose Compose stderr, container labels, config or credential values.
        raise PreflightHold("READ_ONLY_COMMAND_FAILED") from None
    return response.stdout


def _labels() -> tuple[list[str], str]:
    raw = _run(["docker", "inspect", "--format", "{{json .Config.Labels}}", CONTAINER])
    try:
        labels = json.loads(raw)
        files = labels["com.docker.compose.project.config_files"].split(",")
        directory = labels["com.docker.compose.project.working_dir"]
        service = labels["com.docker.compose.service"]
        if service != "api" or not (1 <= len(files) <= 8):
            raise ValueError("not the expected Compose service")
        if not all(f.startswith("/") and Path(f).is_file() for f in files):
            raise ValueError("unresolved Compose files")
        if not Path(directory).is_dir():
            raise ValueError("unresolved working directory")
        return files, directory
    except (ValueError, KeyError, TypeError, AttributeError):
        raise PreflightHold("COMPOSE_PROVENANCE_UNRESOLVED") from None


def api_source_free_mounts(model: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        volumes = model["services"]["api"]["volumes"]
        if not isinstance(volumes, list):
            raise ValueError("invalid volume list")
        offending = [
            v for v in volumes
            if isinstance(v, dict) and v.get("target") == SOURCE_TARGET
        ]
        if len(offending) != 1 or offending[0].get("type") != "bind":
            raise ValueError("unexpected source mount count/type")
        if len(volumes) < 2 or any(not isinstance(v, dict) for v in volumes):
            raise ValueError("unsafe or missing volumes")
        return [v for v in volumes if v.get("target") != SOURCE_TARGET]
    except (KeyError, TypeError, ValueError):
        raise PreflightHold("UNEXPECTED_SOURCE_BIND_TOPOLOGY") from None


def assert_only_api_source_removed(
    before: dict[str, Any], after: dict[str, Any], retained: list[dict[str, Any]]
) -> dict[str, Any]:
    try:
        old = before["services"]["api"]
        new = after["services"]["api"]
        if new["volumes"] != retained:
            raise ValueError("retained mount drift")
        if [v for v in new["volumes"] if v.get("target") == SOURCE_TARGET]:
            raise ValueError("source override still present")
        other_old = {k: v for k, v in old.items() if k != "volumes"}
        other_new = {k: v for k, v in new.items() if k != "volumes"}
        if other_old != other_new:
            raise ValueError("API nonvolume config drift")
        stripped_before = dict(before)
        stripped_after = dict(after)
        stripped_before["services"] = dict(before["services"])
        stripped_after["services"] = dict(after["services"])
        stripped_before["services"]["api"] = other_old
        stripped_after["services"]["api"] = other_new
        if stripped_before != stripped_after:
            raise ValueError("another service, volume or network changed")
        if len(old["volumes"]) != len(retained) + 1:
            raise ValueError("incorrect mount delta")
        return {
            "verdict": "API_ONLY_SOURCE_BIND_REMOVAL_RENDER_PASS_NOT_DEPLOYMENT",
            "service": "api",
            "old_mount_count": len(old["volumes"]),
            "new_mount_count": len(new["volumes"]),
            "removed_source_mount_count": 1,
            "all_other_services_equal": True,
            "all_other_mounts_equal": True,
            "api_nonvolume_config_equal": True,
            "docker_images_imported": 0,
            "production_files_written": 0,
            "production_containers_restarted": 0,
            "physical_api_startup_verified": False,
            "physical_rollback_verified": False,
            "release_authorized": False,
        }
    except (ValueError, KeyError, TypeError):
        raise PreflightHold("RENDERED_COMPOSE_DRIFT_HOLD") from None


def preflight() -> dict[str, Any]:
    files, directory = _labels()
    compose = ["docker", "compose"]
    for file in files:
        compose.extend(["-f", file])
    model = json.loads(_run(compose + ["config", "--format", "json"], cwd=directory))
    retained = api_source_free_mounts(model)
    overlay = "services:\n  api:\n    volumes: !override\n"
    overlay += "".join(
        "      " + line + "\n"
        for line in yaml.safe_dump(retained, sort_keys=False).splitlines()
    )
    rendered = json.loads(
        _run(compose + ["-f", "-", "config", "--format", "json"],
             cwd=directory, stdin=overlay)
    )
    return assert_only_api_source_removed(model, rendered, retained)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", required=True)
    parser.parse_args()
    try:
        print(json.dumps(preflight(), sort_keys=True))
        return 0
    except (PreflightHold, json.JSONDecodeError) as e:
        reason = str(e) if isinstance(e, PreflightHold) else "INVALID_COMPOSE_JSON"
        print(json.dumps({"verdict": "API_ONLY_SOURCE_BIND_PREFLIGHT_HOLD",
                          "reason": reason, "release_authorized": False},
                         sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
