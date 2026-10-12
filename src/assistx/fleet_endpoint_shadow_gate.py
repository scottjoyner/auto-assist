"""Pure, deny-only fleet admission observations and mobile source binding.

Neither a self-reported provider lease snapshot nor a mobile projection can
authorize work. Live authorization belongs to an authenticated coordinator.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from .fleet_endpoint_harness import _timestamp

ALIASES = {"kilo_free": "kilo", "kilo_anonymous": "kilo", "kilo": "kilo"}
ALLOWED_STATES = {"resident_verified": 3, "advertised_unverified": 2,
                  "installed_not_loaded": 1}
MAX_RAW_BYTES = 2_000_000
HASH = re.compile(r"[0-9a-f]{64}")


def _checked_nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _fresh(epoch: Any, now: float, ttl: int) -> bool:
    stamp = _timestamp(epoch)
    return 0 < stamp <= now and now - stamp <= ttl


def shadow_quota_assessment(report: dict[str, Any],
                            lease_snapshot: dict[str, Any] | None,
                            *, now: float, max_age_seconds: int = 120) -> dict[str, Any]:
    """Read a past lease status only: no lease acquisition or authorization."""
    if report.get("schema") != "fleet-endpoint-harness-report/v1":
        raise ValueError("unsupported harness report")
    endpoints = report.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) > 200:
        raise ValueError("invalid endpoint list")
    snapshot = lease_snapshot if isinstance(lease_snapshot, dict) else {}
    structurally_valid = (
        snapshot.get("schema") == "provider-lease-status/v1"
        and snapshot.get("authority") == "single_host_prototype_only"
        and snapshot.get("audit_integrity") is True
        and isinstance(snapshot.get("providers"), dict)
        and _fresh(snapshot.get("captured_at"), now, max_age_seconds)
    )
    entries = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise ValueError("invalid endpoint row")
        provider = endpoint.get("provider")
        kind = endpoint.get("kind")
        group = ALIASES.get(provider) if isinstance(provider, str) else None
        reasons = ["missing_authenticated_cross_node_lease",
                   "upstream_billing_and_quota_not_verified"]
        if kind != "cloud_chat":
            reasons = ["requires_separate_signed_local_runtime_admission"]
        elif group is None:
            reasons.append("unknown_upstream_quota_alias")
        if endpoint.get("classification") != "local_receipt_pass" and kind == "cloud_chat":
            reasons.append("no_fresh_locally_verified_execution")
        local_capacity = False
        if kind == "cloud_chat":
            if not structurally_valid:
                reasons.append("lease_snapshot_untrusted_or_stale")
            elif group is not None:
                row = snapshot["providers"].get(group)
                if not isinstance(row, dict):
                    reasons.append("lease_group_not_observed")
                else:
                    slots, active = row.get("slots"), row.get("active")
                    if (not isinstance(slots, int) or isinstance(slots, bool)
                            or slots <= 0 or not isinstance(active, int)
                            or isinstance(active, bool) or active < 0
                            or type(row.get("circuit_open")) is not bool):
                        reasons.append("invalid_lease_group_counters")
                    elif row["circuit_open"] is True:
                        reasons.append("lease_circuit_open")
                    elif active >= slots:
                        reasons.append("local_lease_slots_full")
                    else:
                        local_capacity = True
        entries.append({
            "id": endpoint.get("id"),
            "provider": provider,
            "model": endpoint.get("model"),
            "admission_decision": "DENY",
            "may_dispatch": False,
            "local_capacity_observed": local_capacity,
            "quota_group": group if group is not None else "unverified",
            "reasons": reasons,
        })
    return {
        "schema": "fleet-endpoint-shadow-admission/v1",
        "mode": "deny_only",
        "coordinator_authenticated": False,
        "distributed_fencing_verified": False,
        "live_lease_actions": 0,
        "provider_requests": 0,
        "authorizations_granted": 0,
        "endpoints": entries,
    }


def _names_from_raw(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Independently reconstruct the mobile's sanitized aggregate identity."""
    indexed: dict[str, dict[str, Any]] = {}
    for endpoint in raw["observations"]:
        for model in endpoint.get("models") or []:
            if not isinstance(model, dict) or model.get("state") not in ALLOWED_STATES:
                continue
            identifier = model.get("id")
            if not isinstance(identifier, str):
                continue
            name = identifier.replace("\\", "/").split("/")[-1].strip()[:100]
            if (not name or ".." in name or "@" in name or "://" in identifier
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ +:\-]{0,99}", name)
                    or re.match(r"^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?$", name)):
                continue
            key = name.casefold()
            record = indexed.setdefault(key, {
                "display_name": name, "state": model["state"], "observed_instances": 0,
                "admitted": False, "selectable": False,
            })
            record["observed_instances"] += 1
            if ALLOWED_STATES[model["state"]] > ALLOWED_STATES[record["state"]]:
                record["state"] = model["state"]
    return sorted(indexed.values(), key=lambda x: x["display_name"].casefold())


def bind_mobile_projection(public: dict[str, Any], private_witness_bytes: bytes,
                           *, now: float, max_age_seconds: int = 120) -> dict[str, Any]:
    """Verify source equality, not the separate HTTP authentication boundary."""
    rejected = {
        "schema": "fleet-mobile-source-binding/v1", "status": "REJECT",
        "source_binding_valid": False, "transport_authenticated": False,
        "execution_authorized": False, "runnable_models": 0,
        "reasons": [],
    }

    def reject(reason: str) -> dict[str, Any]:
        return {**rejected, "reasons": [reason]}

    if not isinstance(private_witness_bytes, bytes) or len(private_witness_bytes) > MAX_RAW_BYTES:
        return reject("raw_witness_missing_or_too_large")
    try:
        private = json.loads(private_witness_bytes)
    except (ValueError, UnicodeError):
        return reject("invalid_raw_witness")
    if not isinstance(private, dict) or not isinstance(public, dict):
        return reject("invalid_contract")
    if (private.get("schema_version") != 1
            or private.get("authority") != "observational_only_not_runtime_admission"):
        return reject("untrusted_private_witness")
    if not _fresh(private.get("captured_at"), now, max_age_seconds):
        return reject("private_witness_stale")
    rows = private.get("observations")
    source_digest = private.get("source_inventory_sha256")
    if (not isinstance(rows, list) or len(rows) > 512
            or any(not isinstance(row, dict) for row in rows)
            or not isinstance(source_digest, str) or not HASH.fullmatch(source_digest)):
        return reject("invalid_private_witness")
    public_fields = {
        "schema_version", "source", "state", "authority",
        "observed_nodes", "observed_endpoints", "resident_endpoints",
        "models", "truncated", "observation_sha256", "source_inventory_sha256",
    }
    if set(public) != public_fields:
        return reject("unrecognized_public_projection_fields")
    if (public.get("schema_version") != 1
            or public.get("source") != "tailnet_observational_only"
            or public.get("authority") != "not_admitted"
            or public.get("state") != "fresh"):
        return reject("public_projection_not_fresh_or_observational")
    expected_digest = hashlib.sha256(private_witness_bytes).hexdigest()
    if public.get("observation_sha256") != expected_digest:
        return reject("private_public_observation_digest_mismatch")
    if public.get("source_inventory_sha256") != source_digest:
        return reject("private_public_source_digest_mismatch")
    node_count = len({
        row["node"] for row in rows
        if isinstance(row.get("node"), str) and row["node"]
    })
    if (type(public.get("observed_nodes")) is not int
            or public["observed_nodes"] != node_count
            or type(public.get("observed_endpoints")) is not int
            or public["observed_endpoints"] != len(rows)
            or type(public.get("resident_endpoints")) is not int
            or public["resident_endpoints"] != sum(
                row.get("state") == "resident_verified" for row in rows)):
        return reject("observation_counts_disagree")
    actual = public.get("models")
    if not isinstance(actual, list) or len(actual) > 80:
        return reject("malformed_public_models")
    expected = _names_from_raw(private)
    if actual != expected[:80] or public.get("truncated") is not (len(expected) > 80):
        return reject("public_models_not_source_bound")
    return {
        **rejected, "status": "LOCAL_SOURCE_BINDING_PASS",
        "source_binding_valid": True, "reasons": [],
        "private_witness_sha256": expected_digest,
        "source_inventory_sha256": source_digest,
        "sanitized_model_count": len(actual),
    }
