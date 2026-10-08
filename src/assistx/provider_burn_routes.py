"""Authenticated, bounded read-only projection of precomputed provider burn data.

The HTTP handler never reads SQLite, calls providers, or changes admission state.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

DEFAULT_REPORT = Path.home() / ".local/state/auto-assist/free-endpoint-burn.json"
MAX_REPORT_BYTES = 4 * 1024 * 1024
FRESHNESS_SECONDS = 3600


def _num(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _limited(value: Any, limit: int = 140) -> str:
    return str(value or "unknown")[:limit]


def _unavailable(reason: str) -> dict[str, Any]:
    return {"schema": "assistx-provider-burn/v1", "read_only": True,
            "state": "unavailable", "reason": reason, "updated_epoch": None,
            "complete": False, "providers": [], "models": [],
            "observed_tokens_24h": None, "forecast_next_24h": None,
            "quota_remaining": None, "free_entitlement_verified": False}


def project_burn_report(path: Path = DEFAULT_REPORT, *, now: float | None = None):
    """Only explicitly allowlisted aggregates reach the authenticated client."""
    now = time.time() if now is None else float(now)
    try:
        if not path.is_file():
            return _unavailable("snapshot_missing")
        if path.stat().st_size > MAX_REPORT_BYTES:
            return _unavailable("snapshot_too_large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _unavailable("snapshot_unreadable")
    if not isinstance(data, dict) or data.get("schema") != "free-endpoint-burn-dashboard/v1":
        return _unavailable("schema_mismatch")
    stamp = data.get("generated_epoch")
    if (type(stamp) not in (int, float) or not math.isfinite(stamp)
            or stamp <= 0 or stamp > now + 60):
        return _unavailable("invalid_timestamp")
    provider_data = data.get("by_provider")
    model_data = data.get("models")
    if not isinstance(provider_data, list) or not isinstance(model_data, list):
        return _unavailable("invalid_report_shape")
    providers = []
    for row in provider_data[:64]:
        if not isinstance(row, dict):
            continue
        classes = row.get("token_classes_24h")
        classes = classes if isinstance(classes, dict) else {}
        providers.append({
            "provider": _limited(row.get("provider")),
            "presence": _limited(row.get("presence")),
            "observed_tokens_1h": _num(row.get("observed_tokens_1h")),
            "observed_tokens_24h": _num(row.get("observed_tokens_24h")),
            "observed_tokens_7d": _num(row.get("observed_tokens_7d")),
            "forecast_next_24h": _num(row.get("forecast_next_24h")),
            "cache_read_24h": _num(classes.get("cache_read")),
            "planned_tokens_per_day": _num(row.get("planned_tokens_per_day")),
            "quota_remaining": None,
        })
    models = []
    for row in model_data[:160]:
        if not isinstance(row, dict):
            continue
        windows = row.get("windows")
        windows = windows if isinstance(windows, dict) else {}
        last24 = windows.get("24h")
        last24 = last24 if isinstance(last24, dict) else {}
        last7 = windows.get("7d")
        last7 = last7 if isinstance(last7, dict) else {}
        models.append({
            "provider": _limited(row.get("provider")),
            "model": _limited(row.get("model"), 180),
            "free_name_hint_only": row.get("name_suggests_free") is True,
            "tokens_24h": _num(last24.get("total")),
            "tokens_7d": _num(last7.get("total")),
            "age_minutes": (round(row["age_minutes"], 1)
                            if type(row.get("age_minutes")) in (int, float)
                            and 0 <= row["age_minutes"] < 10**7 else None),
        })
    stale = now - stamp > FRESHNESS_SECONDS
    complete = data.get("complete") is True
    total = sum(r["observed_tokens_24h"] or 0 for r in providers
                if r["observed_tokens_24h"] is not None)
    return {"schema": "assistx-provider-burn/v1", "read_only": True,
            "state": "stale" if stale else "fresh",
            "updated_epoch": stamp, "age_seconds": round(now - stamp),
            "complete": complete, "sources": len(data.get("coverage") or []),
            "step_events": _num(data.get("step_events")),
            "observed_tokens_24h": total if complete else None,
            "forecast_next_24h": total if complete else None,
            "quota_remaining": None, "free_entitlement_verified": False,
            "providers": providers, "models": models}


def build_provider_burn_router(auth_dependency, templates, *, report_path=None):
    router = APIRouter(tags=["provider-burn"])
    report_path = Path(report_path) if report_path is not None else Path(
        os.environ.get("ASSISTX_FREE_BURN_REPORT_PATH", str(DEFAULT_REPORT)))

    @router.get("/api/fleet/provider-burn")
    def provider_burn_api(_: str = Depends(auth_dependency)):
        result = project_burn_report(report_path)
        return JSONResponse(content=result, headers={"Cache-Control": "private, no-store",
                                                      "X-Content-Type-Options": "nosniff"})

    @router.get("/provider-usage", response_class=HTMLResponse)
    def provider_burn_page(request: Request, _: str = Depends(auth_dependency)):
        return templates.TemplateResponse(
            request=request, name="provider_usage.html",
            context={"request": request}, headers={"Cache-Control": "private, no-store"})
    return router
