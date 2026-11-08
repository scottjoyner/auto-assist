"""Read-only, fail-closed pre-claim and pre-inference fleet dispatch boundary.

This is a necessary control-plane consistency witness, NOT an independently
resource-owned slot reservation. Missing, expired, mismatched or pre-signed
canonical graph authority must deny every new inference effect.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .work_supply import STATE_FRESHNESS_SECONDS


@dataclass(frozen=True)
class SupplyGate:
    allowed: bool
    reason: str


def _exact_positive(value: Any) -> int:
    """Neo4j integers only, rejecting bools, floats and string coercion."""
    return value if type(value) is int and value > 0 else 0


def current_supply_permits_effect(neo: Any, projection: Any, *, now_ms: int | None = None) -> SupplyGate:
    """Demand matching fresh canonical + active WorkSupply, with no fallback.

    This read cannot grant Task authority, reserve slots, sign an approval or
    create graph nodes. It is deliberately checked again just before each
    router model invocation; a stale cached projection is insufficient.
    """
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if type(now) is not int or now <= 0:
        return SupplyGate(False, "invalid_clock")
    try:
        generation = _exact_positive(projection.generation)
        slots = _exact_positive(projection.total_slots)
        if not generation or not slots or _exact_positive(projection.expires_at_ms) <= now:
            return SupplyGate(False, "cached_projection_invalid")
    except (AttributeError, TypeError, ValueError):
        return SupplyGate(False, "cached_projection_invalid")

    try:
        with neo._session() as session:
            row = session.run(
                """
                MATCH (p:FleetProjectionState {name:'canonical'})
                OPTIONAL MATCH (w:WorkSupplyState {name:'canonical'})
                RETURN p.generation AS generation,
                       p.status AS status,
                       p.expires_at_ts AS expires_at_ts,
                       p.owner_approval_receipt_sha256 AS owner_receipt,
                       w.generated_at_ts AS supply_generated_at_ts,
                       w.projection_generation AS supply_generation,
                       w.total_slots AS supply_slots
                LIMIT 2
                """
            ).single(strict=True)
    except Exception:
        return SupplyGate(False, "authoritative_graph_unavailable")
    if not row:
        return SupplyGate(False, "canonical_state_missing")
    try:
        r = dict(row)
        approved = r.get("status") == "approved"
        owner_receipt = r.get("owner_receipt")
        root_authorized = type(owner_receipt) is str and re.fullmatch(r"[0-9a-f]{64}", owner_receipt) is not None
        canonical_gen = _exact_positive(r.get("generation"))
        canonical_expires = _exact_positive(r.get("expires_at_ts"))
        supply_gen = _exact_positive(r.get("supply_generation"))
        supply_slots = _exact_positive(r.get("supply_slots"))
        supply_time = _exact_positive(r.get("supply_generated_at_ts"))
    except (TypeError, ValueError):
        return SupplyGate(False, "canonical_state_malformed")
    if not approved or not root_authorized:
        return SupplyGate(False, "canonical_owner_grant_missing")
    if canonical_gen != generation or canonical_expires <= now:
        return SupplyGate(False, "canonical_generation_or_lease_changed")
    if supply_gen != generation or supply_slots != slots:
        return SupplyGate(False, "work_supply_generation_or_capacity_mismatch")
    if not supply_time or supply_time > now + 5_000:
        return SupplyGate(False, "work_supply_clock_invalid")
    # Never allow an accidentally enormous freshness environment override to
    # convert a stale controller into permission to execute. Max 120 seconds.
    if now - supply_time > min(STATE_FRESHNESS_SECONDS, 120) * 1000:
        return SupplyGate(False, "work_supply_heartbeat_expired")
    return SupplyGate(True, "matching_live_controller_and_owner_receipt")
