"""Sanitize observation-only Tailnet model witnesses for the offline harness.

This adapter never turns installed or resident models into selectable handles.
It strips host/IP/port details and does not perform network discovery.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .fleet_endpoint_harness import _exact_model, _timestamp

NATIVE_PROOFS = {"lmstudio_native_loaded_instances", "llamacpp_health_props"}
KIND_MAP = {"chat_model": ("local_chat", "chat"),
            "embedding_model": ("embedding", "embedding")}


def adapt_tailnet_witness(witness: dict[str, Any]) -> tuple[dict, dict, dict]:
    if (witness.get("schema_version") != 1
            or witness.get("authority") != "observational_only_not_runtime_admission"):
        raise ValueError("unsupported or authoritative Tailnet witness")
    captured = _timestamp(witness.get("captured_at"))
    rows = witness.get("observations")
    if not isinstance(rows, list) or len(rows) > 300 or not captured:
        raise ValueError("invalid Tailnet witness size or timestamp")

    endpoints: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid Tailnet observation row")
        models = row.get("models") or []
        if not isinstance(models, list) or len(models) > 100:
            raise ValueError("invalid Tailnet observation model list")
        for model in models:
            if not isinstance(model, dict):
                skipped += 1
                continue
            model_kind = model.get("model_kind")
            kind = KIND_MAP.get(model_kind) if isinstance(model_kind, str) else None
            model_id = model.get("id")
            if kind is None or not isinstance(model_id, str) or not _exact_model(model_id):
                skipped += 1
                continue
            locator_parts = (
                row.get("node"), row.get("service"), row.get("port"), model_id
            )
            if any(value is None for value in locator_parts):
                skipped += 1
                continue
            identity = "|".join(str(x) for x in locator_parts)
            endpoint_id = "tailnet-" + hashlib.sha256(identity.encode()).hexdigest()[:20]
            if endpoint_id in seen:
                skipped += 1
                continue
            seen.add(endpoint_id)
            loaded = (model.get("state") == "resident_verified"
                      and row.get("state") == "resident_verified")
            proof = model.get("proof") in NATIVE_PROOFS
            endpoints.append({
                "id": endpoint_id, "provider": "tailnet_native",
                "kind": kind[0], "model": model_id,
                "upstream_group": "unverified",
            })
            observations.append({
                "endpoint_id": endpoint_id, "model": model_id,
                "model_kind": kind[1], "observed_epoch": captured,
                "health": "ok" if loaded else "unknown",
                "loaded": loaded, "props_confirmed": bool(loaded and proof),
                "canary_pass": False,
            })

    return (
        {"schema": "fleet-endpoint-inventory/v1", "endpoints": endpoints},
        {"schema": "fleet-local-observations/v1", "observations": observations},
        {
            "schema": "fleet-tailnet-adapter-summary/v1",
            "candidates": len(endpoints), "skipped_unknown_or_duplicate": skipped,
            "witness_authority": "observation_only",
            "canaries_performed": 0, "addresses_exported": 0,
        },
    )
