"""Authenticated read-only SSD/NAS pressure projection for AssistX.

This endpoint NEVER reads original fleet files, metrics spools or databases.
It only loads the bounded sanitized artifact generated on x1-370.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

DEFAULT_REPORT = Path("/app/artifacts/storage-pressure-status.json")
SCHEMA = "assistx-fleet-storage-pressure/v1"
MAX_BYTES = 8192
MAX_AGE_SECONDS = 1800
VALID_ADMISSION = {"GO", "HOLD", "STOP", "THROTTLE", "UNKNOWN"}
VALID_SEVERITIES = {"healthy", "warning", "critical", "unknown"}

def _positive_number(value: Any) -> float | int | None:
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) and 0 <= value < 1e30 else None
    except (OverflowError, ValueError):
        return None

def unavailable(reason: str) -> dict:
    return {"schema": SCHEMA, "read_only": True, "state": "unavailable",
            "reason": reason, "observation_ok": False, "observed_epoch": None,
            "ssd_severity": "unknown", "ssd_used_percent": None,
            "ssd_available_bytes": None, "nas_available_bytes": None,
            "nas_used_percent": None, "nas_l5_admission": "UNKNOWN",
            "nas_l5_observed_go": False,
            "nas_new_background_copy_permitted": False,
            "nas_btrfs_error_count": None, "root_census_complete": False,
            "published_nas_manifest_bytes": None,
            "source_release_blocked": True, "automatic_cleanup_allowed": False,
            "reclaimed_bytes": 0}

def project_storage_report(path: Path = DEFAULT_REPORT, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    try:
        if not path.is_file():
            return unavailable("snapshot_missing")
        if path.stat().st_size > MAX_BYTES:
            return unavailable("snapshot_too_large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return unavailable("snapshot_unreadable")
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return unavailable("schema_mismatch")
    stamp = _positive_number(data.get("observed_epoch"))
    if stamp is None or stamp < 1 or stamp > now + 60:
        return unavailable("invalid_timestamp")
    admission = data.get("nas_l5_admission")
    if not isinstance(admission,str) or admission not in VALID_ADMISSION:
        admission = "UNKNOWN"
    severity = data.get("ssd_severity")
    if not isinstance(severity,str) or severity not in VALID_SEVERITIES:
        severity = "unknown"
    age = now - stamp
    fresh = age <= MAX_AGE_SECONDS
    ok = (data.get("observation_ok") is True
          and severity in ("warning","critical","healthy")
          and _positive_number(data.get("ssd_available_bytes")) is not None
          and _positive_number(data.get("nas_available_bytes")) is not None
          and _positive_number(data.get("ssd_used_percent")) is not None
          and _positive_number(data.get("nas_used_percent")) is not None)
    state = ("fresh" if fresh and ok else
             "stale" if not fresh else "degraded")
    ssd_used = _positive_number(data.get("ssd_used_percent"))
    nas_used = _positive_number(data.get("nas_used_percent"))
    if ssd_used is not None and ssd_used > 100: ssd_used = None
    if nas_used is not None and nas_used > 100: nas_used = None
    observed_go = state == "fresh" and admission == "GO" and ok
    return {
        "schema": SCHEMA, "read_only": True, "state": state,
        "observed_epoch": stamp, "age_seconds": round(max(0,age)),
        "observation_ok": ok and fresh,
        "ssd_severity": severity if ok and fresh else "unknown",
        "ssd_used_percent": ssd_used if ok and fresh else None,
        "ssd_available_bytes": _positive_number(data.get("ssd_available_bytes")) if ok and fresh else None,
        "nas_available_bytes": _positive_number(data.get("nas_available_bytes")) if ok and fresh else None,
        "nas_used_percent": nas_used if ok and fresh else None,
        "nas_l5_admission": admission if fresh else "UNKNOWN",
        "nas_l5_observed_go": observed_go,
        # A sampled GO never authorizes execution: a new write must obtain
        # a new admission decision directly from the guarded offload worker.
        "nas_new_background_copy_permitted": False,
        "nas_btrfs_error_count": _positive_number(data.get("nas_btrfs_error_count")) if fresh else None,
        "root_census_complete": data.get("root_census_complete") is True,
        "published_nas_manifest_bytes": _positive_number(data.get("published_nas_manifest_bytes")) if fresh else None,
        "source_release_blocked": True,
        "automatic_cleanup_allowed": False,
        "reclaimed_bytes": 0,
    }

def build_storage_pressure_router(auth_dependency, *, report_path: Path | None = None):
    router = APIRouter(tags=["fleet-storage"])
    path = report_path or Path(
        os.environ.get("ASSISTX_STORAGE_PRESSURE_REPORT_PATH", str(DEFAULT_REPORT)))

    @router.get("/api/fleet/storage-pressure")
    def storage_pressure_api(_: str = Depends(auth_dependency)):
        return JSONResponse(content=project_storage_report(path),
                            headers={"Cache-Control": "private, no-store",
                                     "X-Content-Type-Options": "nosniff"})
    return router
