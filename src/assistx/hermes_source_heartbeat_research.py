"""Opt-in ledger-enrolled Hermes lease heartbeat candidate; NO-GO for fleet.

A fixed, DB-clock and current-claim-bound Task renewal mutation sharing
HermesSourceGeneration's transaction lock with research-only completion.
Never loaded by production API/worker/adapter, never dispatch-authoritative.
"""
from __future__ import annotations

import re
from typing import Any

from .hermes_source_generation_research import (
    OWNER,
    GenerationProtocolError,
    advance_candidate_generation,
)

_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MIN_TTL_MS = 1000
MAX_TTL_MS = 60000

# Ledger first, Task second: do not invert acquisition order.
# Only Neo4j's own timestamp may determine live lease eligibility.
_RENEW = """
MATCH (t:Task {id:$task_id})
SET t.hermes_fence_check_epoch =
    coalesce(t.hermes_fence_check_epoch,0)+1
WITH t, timestamp() AS db_now
WHERE t.claimed_by=$owner
  AND t.agent_session_id=$session_id
  AND t.claim_id=$claim_id
  AND t.execution_attempt=$attempt
  AND t.status IN ['CLAIMED','RUNNING']
  AND coalesce(t.lease_expires_at_ts,0) > db_now
  AND (t.target_agent_id IS NULL OR t.target_agent_id=$owner)
  AND (t.heartbeat_by IS NULL OR t.heartbeat_by=$owner)
  AND t.completed_by IS NULL
  AND t.failed_by IS NULL
  AND t.paused_from_agent_id IS NULL
  AND t.migration_target_agent_id IS NULL
SET t.heartbeat_by=$owner,
    t.heartbeat_at_ts=db_now,
    t.lease_expires_at_ts=CASE
        WHEN t.lease_expires_at_ts > db_now+$ttl_ms
        THEN t.lease_expires_at_ts ELSE db_now+$ttl_ms END
RETURN t.id AS task_id,
       t.claim_id AS claim_id,
       t.agent_session_id AS session_id,
       t.execution_attempt AS attempt,
       t.status AS status,
       t.heartbeat_by AS heartbeat_by,
       t.heartbeat_at_ts AS heartbeat_at_ts,
       t.lease_expires_at_ts AS lease_expiry,
       db_now AS db_now
"""


def renew_candidate_lease(
    session: Any,
    *,
    expected_generation: int,
    task_id: str,
    claim_id: str,
    session_id: str,
    attempt: int,
    ttl_ms: int,
) -> dict[str, Any]:
    """Renew a current, unexpired claim with a source-ledger revision.

    Rechecks the current owner/claim/session/attempt in the same transaction
    as the new generation. No resource-side lease/effect proof is supplied.
    """
    if (type(task_id) is not str or not _ID.fullmatch(task_id)
            or type(claim_id) is not str or not _ID.fullmatch(claim_id)
            or type(session_id) is not str or not _ID.fullmatch(session_id)
            or type(attempt) is not int or attempt < 1
            or type(ttl_ms) is not int
            or not MIN_TTL_MS <= ttl_ms <= MAX_TTL_MS):
        raise GenerationProtocolError("invalid_heartbeat_identity_or_ttl")

    verified = False
    def mutate(tx: Any) -> bool:
        nonlocal verified
        row = tx.run(_RENEW, {
            "task_id": task_id,
            "claim_id": claim_id,
            "session_id": session_id,
            "attempt": attempt,
            "ttl_ms": ttl_ms,
            "owner": OWNER,
        }).single()
        verified = bool(
            row
            and row.get("task_id") == task_id
            and row.get("claim_id") == claim_id
            and row.get("session_id") == session_id
            and type(row.get("attempt")) is int
            and row.get("attempt") == attempt
            and row.get("status") in ("CLAIMED", "RUNNING")
            and row.get("heartbeat_by") == OWNER
            and type(row.get("heartbeat_at_ts")) is int
            and type(row.get("lease_expiry")) is int
            and type(row.get("db_now")) is int
            and row["heartbeat_at_ts"] == row["db_now"]
            and row["lease_expiry"] >= row["db_now"] + ttl_ms
        )
        return verified

    receipt = advance_candidate_generation(
        session, expected_generation=expected_generation, mutate=mutate,
    )
    if verified is not True:
        raise GenerationProtocolError("heartbeat_ack_not_verified")
    return {
        "schema": 1,
        "owner": OWNER,
        "candidate_generation": receipt["generation"],
        "source_task_lease_readback_in_tx": True,
        "source_generation_writers_complete": False,
        "external_resource_lease_verified": False,
        "external_effects_verified": False,
        "independent_custody_verified": False,
        "production_dispatch_authorized": False,
        "decision": "HOLD",
    }
