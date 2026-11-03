"""Research-only Task REVIEW/PROPOSED to READY compare-and-set prototype.

This module is deliberately NOT routed to an API or enabled for production.
The supplied actor is caller asserted, not independently authenticated; a
future operator endpoint MUST verify its principal, trusted-proxy boundary,
and required approval before invoking it. The global writer census, owner
revocation, signed admission, and independent custody are still HOLD.
"""
from __future__ import annotations

from typing import Any


class ReviewTransitionDenied(RuntimeError):
    """No safe review transition occurred; transaction must roll back."""


_CANONICAL_LOCK = """
MATCH (w:WorkSupplyState {name:'canonical'})
SET w.operator_review_serial=coalesce(w.operator_review_serial,0)+1
RETURN count(w) AS matching_rows
"""

# Compare-and-set is evaluated AFTER acquiring the same canonical
# WorkSupplyState write lock that research claim/cancellation writers use.
# This does not grant machine execution or accept any external effect.
_APPROVAL_CAS = """
MATCH (t0:Task {id:$task_id})
WITH collect(t0) AS matched
WHERE size(matched)=1
UNWIND matched AS t
WITH t
WHERE t.status=$expected_status
  AND t.status IN ['REVIEW','PROPOSED']
  AND t.updated_at_ts=$expected_updated_at_ts
  AND t.approved_by IS NULL
  AND t.review_decision IS NULL
  AND coalesce(t.kind,'') <> 'intent_review'
  AND coalesce(t.execution_attempt,0)=0
  AND t.claim_id IS NULL
  AND t.claimed_by IS NULL
  AND t.agent_session_id IS NULL
  AND t.lease_expires_at_ts IS NULL
  AND t.target_agent_id IS NULL
  AND coalesce(t.machine_authorized,false)=false
  AND t.machine_authorization_generation IS NULL
  AND t.machine_authorization_approval_id IS NULL
  AND t.machine_token_epoch IS NULL
  AND t.projection_generation IS NULL
  AND coalesce(t.hermes_claim_scope,false)=false
  AND coalesce(t.paused,false)=false
  AND coalesce(t.auto_dispatch,false)=false
  AND NOT EXISTS {
    MATCH (t)-[:DISPATCHED_AS]->(:Dispatch)
  }
  AND NOT EXISTS {
    MATCH (t)-[:HAS_ALLOCATION]->(:AllocationReservation)
  }
SET t.status='READY',
    t.approved_by=$actor,
    t.approved_at=datetime(),
    t.approved_at_ts=timestamp(),
    t.operator_review_source=$expected_status,
    t.review_version=coalesce(t.review_version,0)+1,
    t.updated_at=datetime(),
    t.updated_at_ts=timestamp()
RETURN t.id AS task_id, t.status AS status,
       t.approved_by AS approved_by,
       t.updated_at_ts AS updated_at_ts,
       t.review_version AS review_version
"""


def _in_tx(tx: Any, params: dict[str, Any]) -> dict[str, Any]:
    coordination = tx.run(_CANONICAL_LOCK).single()
    if coordination is None or int(coordination.get("matching_rows") or 0) != 1:
        raise ReviewTransitionDenied("canonical_writer_lock_missing_or_duplicate")
    row = tx.run(_APPROVAL_CAS, params).single()
    if row is None:
        raise ReviewTransitionDenied("stale_or_protected_review_task")
    return dict(row)


def propose_operator_review_transition(
    neo: Any, *,
    task_id: str,
    actor: str,
    expected_status: str,
    expected_updated_at_ts: int,
) -> dict[str, Any]:
    """Source-only guarded CAS; caller identity and approval NOT verified here.

    expected_updated_at_ts is a required optimistic concurrency token obtained
    from a trusted read. REVIEW and PROPOSED are distinct workflows; no
    intent_review ticket or scoped/claimed/targeted Task may use this helper.
    """
    if (not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 256
        or not isinstance(actor, str) or not actor.strip()
        or len(actor) > 128
        or expected_status not in ("REVIEW", "PROPOSED")
        or type(expected_updated_at_ts) is not int
        or expected_updated_at_ts <= 0):
        raise ReviewTransitionDenied("invalid_operator_transition_request")
    params = {
        "task_id":task_id, "actor":actor,
        "expected_status":expected_status,
        "expected_updated_at_ts":expected_updated_at_ts,
    }
    with neo._session() as session:
        return session.execute_write(_in_tx, params)
