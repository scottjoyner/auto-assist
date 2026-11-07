#!/usr/bin/env python3
"""Read-only AssistX projection renewal witness. Never creates admission.

Run from the existing API container with `python3 - < script`. It only issues
fixed Cypher MATCH/RETURN queries and one no-redirect local model-list GET.
It NEVER reads token/signer values, renews timestamps or signs projections.
Even with all evidence fresh, this command exits 2 (operator approval HOLD).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

MODEL_URL = "http://host.docker.internal:1234/api/v0/models"

# Fixed, bounded, read-only queries; no externally supplied Cypher or writes.
QUERIES = {
    "canonical": "MATCH (s:FleetProjectionState {name:'canonical'}) RETURN count(s) AS total, coalesce(max(s.generation),0) AS generation, coalesce(max(s.expires_at_ts),0) AS latest_expiry",
    "runtime": "MATCH (s:RuntimeInstance) RETURN count(s) AS total, sum(CASE WHEN s.admitted=true AND coalesce(s.expires_at_ts,0)>$now_ms THEN 1 ELSE 0 END) AS fresh",
    "loaded_models": "MATCH (s:LoadedModelInstance) RETURN count(s) AS total, sum(CASE WHEN s.admitted=true AND coalesce(s.expires_at_ts,0)>$now_ms THEN 1 ELSE 0 END) AS fresh",
    "access_paths": "MATCH (s:AccessPath) RETURN count(s) AS total, sum(CASE WHEN s.approved=true AND coalesce(s.expires_at_ts,0)>$now_ms THEN 1 ELSE 0 END) AS fresh",
    "capacity": "MATCH (s:CapacityObservation) RETURN count(s) AS total, sum(CASE WHEN s.approved=true AND coalesce(s.expires_at_ts,0)>$now_ms AND coalesce(s.parallel_slots,0)>0 THEN 1 ELSE 0 END) AS fresh",
    "work_supply": "MATCH (s:WorkSupplyState {name:'canonical'}) RETURN count(s) AS total, coalesce(sum(CASE WHEN coalesce(s.generated_at_ts,0)>$fresh_cutoff_ms THEN 1 ELSE 0 END),0) AS fresh, coalesce(sum(CASE WHEN coalesce(s.generated_at_ts,0)>$fresh_cutoff_ms AND s.allow_background=true THEN 1 ELSE 0 END),0) AS allowing_background, coalesce(max(s.projection_generation),0) AS projection_generation",
}


def read_graph_snapshot(client: Any, now_ms: int) -> dict[str, dict[str, int]]:
    """Read the six fixed graph counters. Never return arbitrary properties."""
    snapshot: dict[str, dict[str, int]] = {}
    with client._session() as session:
        for key, statement in QUERIES.items():
            params = {"now_ms": now_ms}
            if key == "work_supply":
                # Conservative 60-second liveness witness. WorkSupplyState
                # has no status=approved or expires_at_ts; it is a CONTROLLER
                # decision, not an independently signed capacity approval.
                params["fresh_cutoff_ms"] = now_ms - 60_000
            row = session.run(statement, **params).single()
            if row is None:
                raise ValueError("projection_query_no_result")
            values = dict(row)
            if key == "canonical":
                wanted = ("total", "generation", "latest_expiry")
            elif key == "work_supply":
                wanted = ("total", "fresh", "allowing_background", "projection_generation")
            else:
                wanted = ("total", "fresh")
            item: dict[str, int] = {}
            for field in wanted:
                value = values.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("projection_counter_malformed")
                item[field] = value
            snapshot[key] = item
    return snapshot


def read_local_loaded_models(
    *, opener: Any | None = None, url: str = MODEL_URL,
) -> list[str]:
    """One GET of loaded model catalog; never invoke a completion or load model."""
    if url != MODEL_URL:
        raise ValueError("model_origin_not_pinned")
    client = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    )
    request = urllib.request.Request(url, method="GET")
    with client.open(request, timeout=4) as response:
        if response.status != 200:
            raise ValueError("model_catalog_not_healthy")
        raw = response.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise ValueError("model_catalog_oversized")
    catalog = json.loads(raw)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("data"), list):
        raise ValueError("model_catalog_invalid")
    result: list[str] = []
    for entry in catalog["data"]:
        if not isinstance(entry, dict) or entry.get("state") != "loaded":
            continue
        name = entry.get("id")
        if isinstance(name, str) and 0 < len(name) <= 160:
            result.append(name)
    return sorted(set(result))[:16]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def assess(
    snapshot: dict[str, dict[str, int]], loaded_models: list[str], now_ms: int,
) -> dict[str, Any]:
    """Even perfect counters never confer approval or Task authority."""
    codes: list[str] = []
    canonical = snapshot["canonical"]
    if canonical["total"] != 1 or canonical["generation"] <= 0:
        codes.append("CANONICAL_IDENTITY_UNRESOLVED")
    if canonical["latest_expiry"] <= now_ms:
        codes.append("CANONICAL_APPROVAL_EXPIRED")
    counters: dict[str, dict[str, int]] = {}
    for label in ("runtime", "loaded_models", "access_paths", "capacity"):
        counter = snapshot[label]
        counters[label] = {"total": counter["total"], "fresh_approved": counter["fresh"]}
        if counter["fresh"] <= 0:
            codes.append("NO_FRESH_APPROVED_" + label.upper())

    # WorkSupplyState is a transient scheduler decision, NOT an approval node.
    # Its absence/staleness blocks a scheduler, but a fresh HOLD is evidence
    # that the controller is alive and correctly refusing unavailable slots.
    supply = snapshot["work_supply"]
    counters["work_supply"] = {
        "total": supply["total"],
        "fresh_controller_state": supply["fresh"],
        "allow_background": supply["allowing_background"],
        "projection_generation": supply["projection_generation"],
    }
    if supply["fresh"] <= 0:
        codes.append("NO_FRESH_WORK_SUPPLY_CONTROLLER_STATE")
    elif supply["allowing_background"] <= 0:
        codes.append("WORK_SUPPLY_DISPATCH_HELD")
    if supply["projection_generation"] != canonical["generation"]:
        codes.append("WORK_SUPPLY_PROJECTION_GENERATION_MISMATCH")
    if not loaded_models:
        codes.append("NO_PHYSICAL_LOADED_MODEL_WITNESS")
    codes.append("INDEPENDENT_OWNER_SIGNED_REAPPROVAL_REQUIRED")
    return {
        "schema": "assistx-readonly-renewal-preflight-v1",
        "observed_at_ms": now_ms,
        "source": "read_only_graph_and_local_model_catalog",
        "canonical_generation": canonical["generation"],
        "canonical_expired_hours": round(max(0, now_ms - canonical["latest_expiry"]) / 3600000, 2),
        "graph": counters,
        "physical_loaded_models": loaded_models,
        "physical_loaded_model_count": len(loaded_models),
        "artifact_fingerprint_attested": False,
        "physical_capacity_attested": False,
        "signed_resource_commit_verified": False,
        "approval_candidate_ready": False,
        "task_admission": "HOLD",
        "status": "HOLD",
        "reason_codes": sorted(set(codes)),
    }


def main() -> int:
    now_ms = int(time.time() * 1000)
    output: dict[str, Any] = {
        "schema": "assistx-readonly-renewal-preflight-v1", "status": "HOLD",
        "task_admission": "HOLD", "approval_candidate_ready": False,
        "reason_codes": ["EVIDENCE_COLLECTION_FAILED"],
    }
    try:
        from assistx.api import _neo

        graph = _neo()
        try:
            snapshot = read_graph_snapshot(graph, now_ms)
        finally:
            graph.close()
        model_names = read_local_loaded_models()
        output = assess(snapshot, model_names, now_ms)
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            urllib.error.URLError, json.JSONDecodeError):
        pass  # Never echo exceptions, URL headers, graph rows, tokens or keys.
    print(json.dumps(output, sort_keys=True))
    return 2  # Always non-admitting, including a future all-fresh snapshot.


if __name__ == "__main__":
    sys.exit(main())
