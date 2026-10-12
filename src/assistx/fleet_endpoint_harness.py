"""Offline fleet endpoint evidence harness. No networking, dispatch, or admission.

A 'pass' below denotes a locally reproducible observation, never an
upstream quota, security, billing, or production routing authorization.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 2_000_000
MAX_TRACE_BYTES = 8_000_000
MAX_MANIFESTS = 500
KINDS = {"cloud_chat", "local_chat", "embedding", "gateway"}
EXACT_NAME = re.compile(r"^[A-Za-z0-9_@][A-Za-z0-9_@./:+-]{0,159}$")
ROUTE_ALIASES = {"auto", "default", "free", "best", "any", "router", "latest"}
PRESENCE_ALIASES = {"kilo_free": "kilo_anonymous"}
CLOUD_PRESENCE_OK = {"catalog_reachable"}


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("missing, symlinked, or oversized JSON evidence")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("evidence must be a JSON object")
    return data


def _exact_model(model: str) -> bool:
    if not isinstance(model, str) or not EXACT_NAME.fullmatch(model):
        return False
    return model.rsplit("/", 1)[-1].split(":", 1)[0].lower() not in ROUTE_ALIASES


def _timestamp(value: Any) -> float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value) if value > 0 else 0
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp() if dt.tzinfo else 0
        except ValueError:
            pass
    return 0


def _fresh(value: Any, now: float, max_age: int) -> bool:
    age = now - _timestamp(value)
    return 0 <= age <= max_age


def _safe_trace_path(path_string: Any, state_root: Path) -> Path | None:
    if not isinstance(path_string, str) or not path_string:
        return None
    path = Path(path_string)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        traces_directory = state_root / "traces"
        if traces_directory.is_symlink():
            return None
        resolved = path.resolve(strict=True)
        traces_root = traces_directory.resolve(strict=True)
        if not resolved.is_relative_to(traces_root):
            return None
        if resolved.stat().st_size > MAX_TRACE_BYTES:
            return None
    except (OSError, RuntimeError):
        return None
    return resolved


def _trace_status(manifest: dict[str, Any], state_root: Path) -> str:
    """Recompute local trace integrity, never use manifest status as proof."""
    if manifest.get("status") != "completed":
        return "run_not_completed"
    if manifest.get("model_identity_complete") is not True:
        return "model_identity_unproven"
    cost = manifest.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost != 0:
        return "nonzero_or_unknown_reported_cost"
    stdout = manifest.get("stdout")
    if not isinstance(stdout, dict):
        return "missing_stdout_metadata"
    trace = _safe_trace_path(stdout.get("path"), state_root)
    if trace is None:
        return "missing_or_untrusted_trace"
    expected = stdout.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        return "missing_trace_digest"
    if hashlib.sha256(trace.read_bytes()).hexdigest() != expected:
        return "trace_hash_mismatch"
    saw_text = False
    saw_stop = False
    try:
        with trace.open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                if not isinstance(event, dict):
                    return "malformed_trace"
                if event.get("sessionID") != manifest.get("session_id"):
                    return "trace_session_mismatch"
                typ = event.get("type")
                part = event.get("part")
                part = part if isinstance(part, dict) else {}
                if typ == "error" or part.get("type") == "error":
                    return "trace_error"
                if typ == "text" and isinstance(part.get("text"), str) and part["text"].strip():
                    saw_text = True
                if typ == "step_finish" and part.get("reason") == "stop":
                    saw_stop = True
    except (OSError, ValueError, UnicodeError):
        return "malformed_trace"
    if not saw_text or not saw_stop:
        return "missing_final_text_or_stop"
    return "local_trace_verified"


def _latest_run(provider: str, model: str, state_root: Path, now: float, max_age: int) -> dict[str, Any]:
    directory = state_root / "manifests"
    result: dict[str, Any] = {"status": "no_execution_evidence", "fresh": False}
    if directory.is_symlink() or not directory.is_dir():
        return result
    candidates = []
    for path in directory.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            continue
        if len(candidates) >= MAX_MANIFESTS:
            return {"status": "evidence_inventory_over_limit", "fresh": False}
        candidates.append(path)
    for path in sorted(candidates, key=lambda p: (p.stat().st_mtime, p.name), reverse=True):
        try:
            doc = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if doc.get("provider") != provider or doc.get("requested_model") != model:
            continue
        if doc.get("session_id") != path.stem:
            return {"status": "manifest_session_mismatch", "fresh": False}
        if doc.get("effective_model") != model:
            return {"status": "effective_model_mismatch", "fresh": False}
        result = {
            "status": _trace_status(doc, state_root),
            "fresh": _fresh(path.stat().st_mtime, now, max_age),
            "session_id": doc["session_id"],
            "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        return result
    return result


def _local_observations(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if data.get("schema") != "fleet-local-observations/v1":
        raise ValueError("invalid local observation schema")
    rows = data.get("observations")
    if not isinstance(rows, list):
        raise ValueError("observations must be an array")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("endpoint_id"), str):
            raise ValueError("invalid local observation row")
        key = row["endpoint_id"]
        if key in indexed:
            raise ValueError("duplicate local observation ID")
        indexed[key] = row
    return indexed


def build_report(
    inventory: dict[str, Any],
    *,
    presence: dict[str, Any] | None = None,
    local_observations: dict[str, Any] | None = None,
    state_root: Path,
    now: float | None = None,
    freshness_seconds: int = 900,
) -> dict[str, Any]:
    if inventory.get("schema") != "fleet-endpoint-inventory/v1":
        raise ValueError("invalid endpoint inventory schema")
    sources = inventory.get("endpoints")
    if not isinstance(sources, list) or len(sources) > 200:
        raise ValueError("invalid endpoint inventory size")
    now = time.time() if now is None else now
    if freshness_seconds <= 0 or not math.isfinite(now):
        raise ValueError("invalid freshness inputs")
    if presence is not None:
        if presence.get("schema") != "provider-presence/v1" or not isinstance(presence.get("providers"), dict):
            raise ValueError("invalid passive provider presence schema")
    presence = presence or {}
    observation_index = _local_observations(local_observations) if local_observations else {}
    seen = set()
    results: list[dict[str, Any]] = []
    for entry in sources:
        if not isinstance(entry, dict):
            raise ValueError("invalid endpoint")
        endpoint_id = entry.get("id")
        provider, model, kind = entry.get("provider"), entry.get("model"), entry.get("kind")
        if not all(isinstance(x, str) and EXACT_NAME.fullmatch(x) for x in (endpoint_id, provider)):
            raise ValueError("invalid endpoint identity")
        if endpoint_id in seen or kind not in KINDS or not _exact_model(model):
            raise ValueError("duplicate endpoint, unknown kind, or model alias")
        seen.add(endpoint_id)
        group = entry.get("upstream_group")
        group_known = isinstance(group, str) and group not in ("", "unknown", "unverified")
        result = {
            "id": endpoint_id, "kind": kind, "provider": provider, "model": model,
            "upstream_group_declared": group_known,
            "upstream_group_verified": False,
            "production_admitted": False, "automatic_dispatch_allowed": False,
            "upstream_billing_verified": False, "independent_archive_verified": False,
        }
        if kind == "cloud_chat":
            provider_key = PRESENCE_ALIASES.get(provider, provider)
            record = (presence.get("providers") or {}).get(provider_key, {})
            record = record if isinstance(record, dict) else {}
            is_recent = _fresh(record.get("checked_epoch"), now, freshness_seconds)
            catalog = is_recent and record.get("status") in CLOUD_PRESENCE_OK
            run = _latest_run(provider, model, state_root, now, freshness_seconds)
            result.update({
                "catalog_status": "fresh_catalog" if catalog else "missing_or_stale_catalog",
                "execution": run, "classification": (
                    "local_receipt_pass" if catalog and run["status"] == "local_trace_verified" and run["fresh"]
                    else "historical_receipt_only" if run["status"] == "local_trace_verified"
                    else "not_qualified"
                ),
                "next_gate": "authenticated_shared_upstream_lease_and_billing",
            })
        else:
            obs = observation_index.get(endpoint_id, {})
            is_recent = _fresh(obs.get("observed_epoch"), now, freshness_seconds)
            model_matches = obs.get("model") == model
            healthy = is_recent and model_matches and obs.get("health") == "ok"
            resident = healthy and obs.get("loaded") is True and obs.get("props_confirmed") is True
            canary = resident and obs.get("canary_pass") is True
            if kind == "gateway":
                classification = "gateway_observed" if healthy else "not_qualified"
            elif kind == "embedding":
                classification = (
                    "embedding_canary_observed" if canary and obs.get("model_kind") == "embedding"
                    else "embedding_resident_observed" if resident and obs.get("model_kind") == "embedding"
                    else "not_qualified"
                )
            else:
                classification = "chat_canary_observed" if canary and obs.get("model_kind") == "chat" else (
                    "resident_observed" if resident else "not_qualified"
                )
            result.update({
                "observation_fresh": is_recent,
                "classification": classification,
                "observation_authoritative": False,
                "next_gate": "authenticated_live_completion_and_signed_runtime_admission",
            })
        results.append(result)
    return {
        "schema": "fleet-endpoint-harness-report/v1",
        "generated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "mode": "offline_read_only",
        "network_requests": 0, "inference_requests": 0, "routing_changes": 0,
        "production_admission_grants": 0, "endpoints": results,
    }
