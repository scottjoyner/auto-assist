"""Read-only, bounded candidate inventory of Hermes source Task claims.

This deliberately cannot authorize release: Neo4j read-committed scans are
NOT an atomic generation snapshot, and a repeated equal digest cannot rule
out ABA mutation, unrecorded effects, or a missing independent high-water.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

DOMAIN = b"assistx-hermes-unfenced-census-candidate-v1\0"
OWNER = "hermes-local"
MAX_ROWS = 10000
VALID_STATES = frozenset({"CLAIMED", "RUNNING", "PAUSING", "READY",
                          "DONE", "FAILED", "CANCELLED"})
ACTIVE = frozenset({"CLAIMED", "RUNNING", "PAUSING"})
IDENTITY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Include every currently identifiable Hermes owner and retained history,
# even when claimed_by/target_agent_id, claim_id, and session were cleared.
# An ambiguous historical row must invalidate the candidate digest, not be
# silently absent from both read-committed scans. This does not prove that
# erased history is complete or substitute for independent generation custody.
QUERY = """
MATCH (t:Task)
WHERE coalesce(t.claimed_by, '') STARTS WITH 'hermes-'
   OR coalesce(t.target_agent_id, '') STARTS WITH 'hermes-'
   OR coalesce(t.completed_by, '') STARTS WITH 'hermes-'
   OR coalesce(t.heartbeat_by, '') STARTS WITH 'hermes-'
   OR coalesce(t.failed_by, '') STARTS WITH 'hermes-'
   OR coalesce(t.paused_from_agent_id, '') STARTS WITH 'hermes-'
   OR coalesce(t.migration_target_agent_id, '') STARTS WITH 'hermes-'
   OR coalesce(t.passive_claim_agent_id, '') STARTS WITH 'hermes-'
   OR coalesce(t.last_passive_claim_agent_id, '') STARTS WITH 'hermes-'
RETURN t.id AS task_id, t.claimed_by AS claimed_by,
       t.target_agent_id AS target_agent_id,
       t.claim_id AS claim_id, t.agent_session_id AS session_id,
       t.execution_attempt AS attempt, t.lease_expires_at_ts AS lease_expiry,
       t.status AS status
ORDER BY task_id ASC
LIMIT $limit
"""


def _valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "task_id", "claimed_by", "target_agent_id", "claim_id",
        "session_id", "attempt", "lease_expiry", "status"
    }:
        return False
    if (not isinstance(value["task_id"], str)
            or not IDENTITY.fullmatch(value["task_id"])
            or value["status"] not in VALID_STATES):
        return False
    if value["target_agent_id"] is not None and not isinstance(value["target_agent_id"], str):
        return False
    if value["claimed_by"] != OWNER:
        return False
    if (not isinstance(value["claim_id"], str) or not IDENTITY.fullmatch(value["claim_id"])
            or not isinstance(value["session_id"], str)
            or not IDENTITY.fullmatch(value["session_id"])):
        return False
    if type(value["attempt"]) is not int or value["attempt"] < 1:
        return False
    if type(value["lease_expiry"]) is not int or value["lease_expiry"] < 0:
        return False
    return True


def _pass(session: Any, max_rows: int) -> tuple[list[dict[str, Any]], str]:
    stream = session.run(QUERY, {"agent": OWNER, "limit": max_rows + 1})
    rows: list[dict[str, Any]] = []
    previous = ""
    for entry in stream:
        if len(rows) >= max_rows:
            raise ValueError("bounded_source_inventory_overflow")
        candidate = {key: entry[key] for key in (
            "task_id", "claimed_by", "target_agent_id", "claim_id",
            "session_id", "attempt", "lease_expiry", "status"
        )}
        if not _valid(candidate):
            raise ValueError("claim_identity_or_owner_unqualified")
        if candidate["task_id"] <= previous:
            raise ValueError("claim_inventory_duplicate_or_out_of_order")
        previous = candidate["task_id"]
        rows.append(candidate)
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return rows, hashlib.sha256(DOMAIN + payload).hexdigest()


def candidate_readonly(session_factory: Any, *, max_rows: int = MAX_ROWS) -> dict[str, Any]:
    """Two scans; detects simple churn but NEVER asserts external completeness."""
    reason = "unverified_source_generation_and_external_checkpoint"
    count = 0
    digest = None
    stable = False
    if type(max_rows) is not int or max_rows < 1 or max_rows > MAX_ROWS:
        reason = "invalid_census_bound"
    else:
        try:
            with session_factory() as session:
                first, a = _pass(session, max_rows)
                second, b = _pass(session, max_rows)
            stable = a == b and first == second
            if not stable:
                reason = "source_changed_between_read_committed_scans"
            else:
                count = len(first)
                digest = a
        except Exception:
            # Database timeouts, absent privileges, cursor failures, and
            # invalid source records all fail closed without echoing data.
            reason = "incomplete_or_unqualified_source_claim_inventory"
    return {
        "schema": 1, "mode": "hermes_unfenced_source_claim_census_candidate",
        "owner": OWNER, "bounded_double_scan_stable": stable,
        "candidate_count": count if stable else None,
        "candidate_digest_sha256": digest,
        "reason": reason,
        "source_generation_fenced": False,
        "independently_retained_high_water": False,
        "resource_side_effects_verified": False,
        "authoritative_complete_census": False,
        "production_dispatch_authorized": False,
        "decision": "HOLD",
    }
