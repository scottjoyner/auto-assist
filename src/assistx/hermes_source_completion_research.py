"""Source-generation-enrolled Hermes completion candidate (NO-GO).

A fixed, narrowly scoped Task writer for an opt-in disposable Neo4j database.
Unlike arbitrary callback ACKs, the candidate verifies a matched DB result
inside the source-ledger transaction. It is not wired into live jobs, worker
routes, service configuration or external effect settlement.

Completion is not authorization to dispatch or proof resource-side effects
are settled; the release decision remains HOLD, unconditionally.
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

# The ledger transaction lock is acquired first by advance_candidate_generation
# and then this Task lock. Callers must never use the inverse lock order.
_COMPLETE = """
MATCH (t:Task {id:$task_id})
SET t.hermes_fence_check_epoch =
    coalesce(t.hermes_fence_check_epoch,0)+1
WITH t
WHERE t.claimed_by=$owner
  AND t.agent_session_id=$session_id
  AND t.claim_id=$claim_id
  AND t.execution_attempt=$attempt
  AND t.status='RUNNING'
  AND coalesce(t.lease_expires_at_ts,0) > timestamp()
  AND (t.target_agent_id IS NULL OR t.target_agent_id=$owner)
  AND t.completed_by IS NULL
  AND t.failed_by IS NULL
  AND t.paused_from_agent_id IS NULL
  AND t.migration_target_agent_id IS NULL
SET t.status='DONE',
    t.completed_by=$owner,
    t.completed_at=datetime(),
    t.completed_at_ts=timestamp()
RETURN t.id AS task_id, t.claim_id AS claim_id,
       t.agent_session_id AS session_id,
       t.execution_attempt AS attempt,
       t.status AS status, t.completed_by AS completed_by
"""


def complete_candidate_claim(
    session: Any,
    *,
    expected_generation: int,
    task_id: str,
    claim_id: str,
    session_id: str,
    attempt: int,
) -> dict[str, Any]:
    """Update exactly one active Hermes claim as part of a ledger increment.

    No end-user Task body or claimed external resource receipt is accepted.
    Only source Neo4j readback is checked; all release flags remain false.
    """
    if (type(task_id) is not str or not _ID.fullmatch(task_id)
            or type(claim_id) is not str or not _ID.fullmatch(claim_id)
            or type(session_id) is not str or not _ID.fullmatch(session_id)
            or type(attempt) is not int or attempt < 1
            ):
        raise GenerationProtocolError("invalid_completion_identity")

    verified = False
    def mutate(tx: Any) -> bool:
        nonlocal verified
        row = tx.run(_COMPLETE, {
            "task_id": task_id, "claim_id": claim_id,
            "session_id": session_id, "attempt": attempt,
            "owner": OWNER,
        }).single()
        verified = bool(
            row and row.get("task_id") == task_id
            and row.get("claim_id") == claim_id
            and row.get("session_id") == session_id
            and type(row.get("attempt")) is int
            and row.get("attempt") == attempt
            and row.get("status") == "DONE"
            and row.get("completed_by") == OWNER
        )
        return verified

    receipt = advance_candidate_generation(
        session,
        expected_generation=expected_generation,
        mutate=mutate,
    )
    if verified is not True:
        # Defense in depth: callback and posttransaction checks must agree.
        raise GenerationProtocolError("completion_ack_not_verified")
    return {
        "schema": 1,
        "owner": OWNER,
        "candidate_generation": receipt["generation"],
        "source_task_row_readback_in_tx": True,
        "resource_side_completion_verified": False,
        "source_generation_writers_complete": False,
        "externally_anchored_high_water_verified": False,
        "independent_custody_verified": False,
        "production_dispatch_authorized": False,
        "decision": "HOLD",
    }
