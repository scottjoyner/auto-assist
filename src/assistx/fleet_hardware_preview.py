"""Operator-only, fail-closed read-only preview of a separately configured private fleet-hardware export.

No imports from fleet-hardware, no subprocesses, no network calls, no writes and
NO connection to AssistX dispatch, reservations, Neo4j, or routing mutations.
"""

from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

_SNAPSHOT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$")
_NODE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_MAX_JSON_BYTES = 8 * 1024 * 1024


class FleetHardwareEvidenceError(RuntimeError):
    """Private export unavailable or internally inconsistent."""


def _read_evidence_bytes(root: Path, relative_path: str, *, max_bytes: int) -> bytes:
    """Pin each directory and file descriptor, rejecting symlinks and unsafe types.

    The repo is operator-provided. The local checkout must never be writable by
    untrusted users. File paths are fixed internally, not supplied by HTTP.
    """
    components = Path(relative_path).parts
    if not components or Path(relative_path).is_absolute() or any(x in ("..", ".") for x in components):
        raise FleetHardwareEvidenceError("invalid evidence location")
    directory_fd: int | None = None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        if root.is_symlink():
            raise FleetHardwareEvidenceError("symlinked source root denied")
        directory_fd = os.open(root, flags | os.O_DIRECTORY)
        if os.fstat(directory_fd).st_mode & stat.S_IWOTH:
            raise FleetHardwareEvidenceError("world-writable source root rejected")
        for component in components[:-1]:
            directory = os.open(component, flags | os.O_DIRECTORY, dir_fd=directory_fd)
            if os.fstat(directory).st_mode & stat.S_IWOTH:
                os.close(directory)
                raise FleetHardwareEvidenceError("world-writable evidence directory rejected")
            os.close(directory_fd)
            directory_fd = directory
        descriptor = os.open(components[-1], flags | getattr(os, "O_NONBLOCK", 0), dir_fd=directory_fd)
        try:
            st = os.fstat(descriptor)
            if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
                raise FleetHardwareEvidenceError("evidence type/size invalid")
            if st.st_mode & stat.S_IWOTH:
                raise FleetHardwareEvidenceError("world-writable evidence rejected")
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise FleetHardwareEvidenceError("evidence size invalid")
            return raw
        finally:
            os.close(descriptor)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise FleetHardwareEvidenceError("symlinked evidence or invalid directory denied") from exc
        raise FleetHardwareEvidenceError("evidence unavailable or unsafe") from exc
    except UnicodeError as exc:
        raise FleetHardwareEvidenceError("evidence unavailable or unsafe") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _read_json(root: Path, path: str, sha: dict[str, str]) -> dict[str, Any]:
    try:
        raw = _read_evidence_bytes(root, path, max_bytes=_MAX_JSON_BYTES)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise FleetHardwareEvidenceError("evidence must be JSON object")
        sha[path] = hashlib.sha256(raw).hexdigest()
        return payload
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise FleetHardwareEvidenceError("evidence unavailable") from exc


def _snapshot_root(root: Path) -> str:
    try:
        raw = _read_evidence_bytes(root, "snapshots/LATEST", max_bytes=96)
        snapshot = raw.decode("utf-8").strip()
        if not _SNAPSHOT.fullmatch(snapshot):
            raise FleetHardwareEvidenceError("snapshot name invalid")
        return snapshot
    except UnicodeError as exc:
        raise FleetHardwareEvidenceError("snapshot pointer unavailable") from exc


def hardware_preview(
    root: Path,
    *,
    min_ram_gib: float = 0.0,
    min_gpu_vram_gib: float = 0.0,
    data_host: str | None = None,
    max_age_hours: int = 24,
    limit: int = 50,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Return redacted, non-executable capacity observations for authenticated UI."""
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
           for v in (min_ram_gib, min_gpu_vram_gib)):
        raise ValueError("resource request must be finite")
    if not 0 <= min_ram_gib <= 4096 or not 0 <= min_gpu_vram_gib <= 4096:
        raise ValueError("requested capacity out of bounds")
    if not 1 <= max_age_hours <= 720 or not 1 <= limit <= 100:
        raise ValueError("invalid bounds")
    if data_host is not None and not _NODE.fullmatch(data_host):
        raise ValueError("invalid data host")
    root = Path(root)
    snapshot = _snapshot_root(root)
    timestamp = dt.datetime.strptime(snapshot, "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=dt.UTC)
    clock = now or dt.datetime.now(dt.UTC)
    if clock.tzinfo is None:
        raise ValueError("now must be timezone aware")
    fresh = 0 <= (clock - timestamp).total_seconds() <= max_age_hours * 3600

    digests: dict[str, str] = {}
    resources = _read_json(root, "inventory/fleet-resources.json", digests)
    topo = _read_json(root, "inventory/fleet-topology.json", digests)
    tail = _read_json(root, "inventory/tailscale-nodes.json", digests)
    probe = _read_json(root, f"snapshots/{snapshot}/probe-status.json", digests)
    if _snapshot_root(root) != snapshot:
        raise FleetHardwareEvidenceError("snapshot changed during read")
    if any(
        v != snapshot
        for v in (
            resources.get("snapshot"),
            topo.get("snapshot"),
            tail.get("source_snapshot"),
            probe.get("snapshot"),
        )
    ):
        raise FleetHardwareEvidenceError("cross-source snapshot disagreement")
    if resources.get("read_only_advisory") is not True:
        raise FleetHardwareEvidenceError("nonadvisory source")
    if resources.get("automated_admission_allowed") is not False:
        raise FleetHardwareEvidenceError("source authorization mismatch")
    registry = tail.get("nodes")
    candidates = resources.get("nodes")
    states = probe.get("results")
    edges = topo.get("edges")
    if (
        not isinstance(registry, list)
        or not isinstance(candidates, dict)
        or not isinstance(states, dict)
        or not isinstance(edges, list)
    ):
        raise FleetHardwareEvidenceError("invalid evidence schema")
    registry_names = [n.get("name") for n in registry if isinstance(n, dict)]
    if len(registry_names) != len(registry) or len(set(registry_names)) != len(registry_names):
        raise FleetHardwareEvidenceError("duplicated/malformed registry")
    if set(registry_names) != set(candidates) or set(registry_names) != set(states):
        raise FleetHardwareEvidenceError("registry coverage mismatch")
    if data_host is not None and data_host not in states:
        raise ValueError("unknown data host")

    mount_adjacencies: dict[str, set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("relation") != "REMOTE_MOUNT_TO_HOST":
            continue
        source, target = edge.get("from"), edge.get("to")
        if not isinstance(source, str) or not isinstance(target, str):
            raise FleetHardwareEvidenceError("malformed dependency edge")
        match = re.match(r"^mount/([a-z0-9-]+)/", source)
        if match and target.startswith("host/"):
            client, server = match.group(1), target[5:]
            if client in states and server in states:
                mount_adjacencies.setdefault(client, set()).add(server)

    rows: list[dict[str, Any]] = []
    for name in sorted(candidates):
        source = candidates[name]
        if not isinstance(source, dict):
            raise FleetHardwareEvidenceError("malformed candidate")
        verified = states[name] == "verified"
        expected = "ADVISORY_ONLY_NO_DISPATCH" if verified else "NO_VERIFIED_HARDWARE"
        if source.get("admission_status") != expected:
            raise FleetHardwareEvidenceError("verification mismatch")
        if verified and source.get("observed_snapshot") != snapshot:
            raise FleetHardwareEvidenceError("stale node source")
        reasons: list[str] = []
        evidence: list[str] = []
        if not verified:
            reasons.append("UNVERIFIED_HARDWARE")
        if not fresh:
            reasons.append("STALE_SNAPSHOT")
        ram = source.get("os_visible_ram_gib") if verified else None
        if verified:
            if (isinstance(ram, bool) or not isinstance(ram, (int, float))
                or not math.isfinite(ram) or ram < min_ram_gib):
                reasons.append("RAM_CAPACITY_UNVERIFIED_OR_BELOW_REQUEST")
            elif min_ram_gib > 0:
                evidence.append("OS_VISIBLE_RAM_TOTAL")
        gpu_functions = source.get("gpu_functions", []) if verified else []
        if not isinstance(gpu_functions, list):
            raise FleetHardwareEvidenceError("invalid GPU schema")
        reported_gpu = []
        if min_gpu_vram_gib > 0 and verified:
            for gpu in gpu_functions:
                if not isinstance(gpu, dict):
                    raise FleetHardwareEvidenceError("invalid GPU function")
                vram = gpu.get("driver_vram_gib")
                bdf = gpu.get("pci_bdf")
                if (
                    isinstance(vram, (float, int))
                    and not isinstance(vram, bool)
                    and math.isfinite(vram)
                    and vram >= min_gpu_vram_gib
                    and isinstance(bdf, str)
                    and re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", bdf)
                ):
                    reported_gpu.append(bdf)
            if not reported_gpu:
                reasons.append("NO_INDIVIDUAL_GPU_MEETS_REPORTED_VRAM_REQUEST")
            else:
                evidence.append("GPU_PCI_FUNCTION_VRAM_OBSERVED")
        if data_host is not None:
            if states[data_host] != "verified":
                reasons.append("DATA_HOST_NOT_VERIFIED")
            elif name == data_host:
                evidence.append("LOCAL_HOST_MATCH_ONLY")
            elif data_host in mount_adjacencies.get(name, set()):
                evidence.append("REMOTE_MOUNT_TO_DATA_HOST")
            else:
                reasons.append("DATA_HOST_PATH_UNVERIFIED")
        risks = source.get("risk_flags", []) if verified else []
        if not isinstance(risks, list):
            raise FleetHardwareEvidenceError("invalid risk schema")
        risk_codes = sorted({r.get("code") for r in risks if isinstance(r, dict)
                             and isinstance(r.get("code"), str)
                             and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", r["code"])})
        rows.append(
            {
                "node_id": name,
                "verification": "VERIFIED" if verified else "UNKNOWN",
                "reported_os_ram_gib": ram,
                "reported_gpu_pci_functions_matching_request": sorted(reported_gpu),
                "risks": risk_codes,
                "data_host_relations": sorted(mount_adjacencies.get(name, set())),
                "static_evidence_state": "PLAUSIBLE_NOT_ADMISSIBLE" if not reasons else "INCOMPLETE",
                "blockers": reasons,
                "evidence": evidence,
                "dispatch": "DENIED_NO_AUTHORITY",
            }
        )
    rows.sort(key=lambda row: (bool(row["blockers"]), row["node_id"]))
    canonical = json.dumps({"snapshot": snapshot, "sha256": digests}, sort_keys=True).encode()
    return {
        "contract_version": 1,
        "mode": "READ_ONLY_OPERATOR_PREVIEW",
        "snapshot": snapshot,
        "snapshot_freshness": "FRESH" if fresh else "STALE",
        "evidence_digest_sha256": hashlib.sha256(canonical).hexdigest(),
        "admission_allowed": False,
        "dispatch_allowed": False,
        "checked_nodes": len(rows),
        "returned_nodes": min(len(rows), limit),
        "truncated": len(rows) > limit,
        "requirements": {
            "ram_gib": min_ram_gib,
            "gpu_vram_gib": min_gpu_vram_gib,
            "data_host": data_host,
        },
        "nodes": rows[:limit],
        "warnings": [
            "Static OS RAM is not free RAM; GPU device VRAM is not available VRAM.",
            "Mount host adjacency does not prove file-level locality or capacity ownership.",
            "No model compatibility, live runtime state, lease or fencing verification.",
        ],
    }


def build_fleet_hardware_preview_router(
    *,
    auth_dependency: Callable[..., Any],
    root_provider: Callable[[], str] | None = None,
) -> APIRouter:
    """The only route is authenticated, read-only and opt-in by local path."""
    path_provider = root_provider or (lambda: os.getenv("ASSISTX_FLEET_HARDWARE_ROOT", "").strip())
    router = APIRouter(
        prefix="/api/fleet",
        tags=["fleet-hardware-preview"],
        dependencies=[Depends(auth_dependency)],
    )

    @router.get("/hardware-preview")
    def preview(
        response: Response,
        ram_gib: float = Query(0, ge=0, le=4096),
        gpu_vram_gib: float = Query(0, ge=0, le=4096),
        data_host: str | None = Query(None, max_length=80),
        limit: int = Query(50, ge=1, le=100),
    ) -> dict[str, Any]:
        location = path_provider()
        if not location or not Path(location).is_absolute():
            raise HTTPException(status_code=503, detail="hardware evidence not configured")
        try:
            preview_data = hardware_preview(
                Path(location),
                min_ram_gib=ram_gib,
                min_gpu_vram_gib=gpu_vram_gib,
                data_host=data_host,
                limit=limit,
            )
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Vary"] = "Authorization"
            return preview_data
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (FleetHardwareEvidenceError, OSError, TypeError) as exc:
            # Do not leak operator private filesystem paths or raw inventory.
            raise HTTPException(status_code=503, detail="hardware evidence unavailable") from exc

    return router
