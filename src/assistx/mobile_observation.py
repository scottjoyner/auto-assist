"""Sanitized, read-only mobile projection of a private fleet observation file.

Observations never authorize model selection, routing, or execution.
Only the authenticated Tailnet mobile API may call this helper.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ALLOWED = {"resident_verified", "advertised_unverified", "installed_not_loaded"}
_RANK = {"resident_verified": 3, "advertised_unverified": 2, "installed_not_loaded": 1}
_MAX_BYTES = 2_000_000
_MAX_MODELS = 80


def _empty(state: str) -> dict[str, Any]:
    return {"schema_version": 1, "source": "tailnet_observational_only",
            "state": state, "authority": "not_admitted",
            "observed_nodes": 0, "observed_endpoints": 0,
            "resident_endpoints": 0, "models": [], "truncated": False,
            "observation_sha256": None, "source_inventory_sha256": None}
def project_observations(raw: dict[str, Any], *, now: datetime | None = None,
                         max_age_seconds: int = 120) -> dict[str, Any]:
    if raw.get("schema_version") != 1 or raw.get("authority") != "observational_only_not_runtime_admission":
        return _empty("invalid")
    try:
        captured = datetime.fromisoformat(raw["captured_at"].replace("Z", "+00:00"))
        if captured.tzinfo is None:
            return _empty("invalid")
        age = ((now or datetime.now(UTC)) - captured).total_seconds()
    except (KeyError, ValueError, AttributeError, TypeError):
        return _empty("invalid")
    if age > max_age_seconds or age < -30:
        return _empty("stale")
    source_checksum = raw.get("source_inventory_sha256")
    if not isinstance(source_checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", source_checksum):
        return _empty("invalid")
    entries = raw.get("observations")
    if not isinstance(entries, list) or len(entries) > 512:
        return _empty("invalid")
    models: dict[str, dict[str, Any]] = {}
    nodes: set[str] = set()
    resident_endpoints = 0
    for entry in entries:
        if not isinstance(entry, dict) or (
            "admitted" in entry and entry["admitted"] is not False
        ):
            # A raw observation is never an admission assertion. Reject
            # contradictory evidence rather than silently laundering it.
            return _empty("invalid")
        node = entry.get("node")
        if isinstance(node, str) and node:
            nodes.add(node)
        if entry.get("state") == "resident_verified":
            resident_endpoints += 1
        raw_models = entry.get("models") or []
        if not isinstance(raw_models, list) or len(raw_models) > 1000:
            return _empty("invalid")
        # The collector labels an endpoint resident only when at least one
        # model has an independent residency witness. Do not promote an
        # unreachable/stale endpoint using a contradictory nested model flag.
        resident_claims = [m for m in raw_models if isinstance(m, dict)
                           and m.get("state") == "resident_verified"]
        if bool(resident_claims) != (entry.get("state") == "resident_verified"):
            return _empty("invalid")
        for model in raw_models:
            if not isinstance(model, dict) or model.get("state") not in _ALLOWED:
                continue
            identifier = model.get("id")
            if not isinstance(identifier, str):
                continue
            # Native model identifiers can contain filesystem paths; never show
            # directories, physical endpoints, or artifact fingerprints on mobile.
            name = identifier.replace("\\", "/").split("/")[-1].strip()[:100]
            if (not name or ".." in name or "@" in name or "://" in identifier
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ +:\-]{0,99}", name)
                    or re.match(r"^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?$", name)):
                continue
            key = name.casefold()
            existing = models.get(key)
            if existing is None:
                existing = {"display_name": name, "state": model["state"],
                            "observed_instances": 0, "admitted": False,
                            "selectable": False}
                models[key] = existing
            existing["observed_instances"] += 1
            if _RANK[model["state"]] > _RANK[existing["state"]]:
                existing["state"] = model["state"]
    ordered = sorted(models.values(), key=lambda m: m["display_name"].casefold())
    result = _empty("fresh")
    result.update(observed_nodes=len(nodes), observed_endpoints=len(entries),
                  resident_endpoints=resident_endpoints,
                  models=ordered[:_MAX_MODELS],
                  truncated=len(ordered) > _MAX_MODELS,
                  source_inventory_sha256=source_checksum)
    return result


def load_observations(path: str | None, *, now: datetime | None = None) -> dict[str, Any]:
    if not path:
        return _empty("not_configured")
    try:
        target = Path(path)
        if not target.is_file() or target.stat().st_size > _MAX_BYTES:
            return _empty("unavailable")
        data = target.read_bytes()
        raw = json.loads(data)
        if not isinstance(raw, dict):
            return _empty("invalid")
        result = project_observations(raw, now=now)
        if result["state"] == "fresh":
            result["observation_sha256"] = hashlib.sha256(data).hexdigest()
        return result
    except (OSError, ValueError, UnicodeError, TypeError):
        return _empty("unavailable")