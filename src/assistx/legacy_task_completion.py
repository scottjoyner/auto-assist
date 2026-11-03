"""Research-only pre-commit legacy Task completion ownership fence.

This is NOT machine accepted-result authority or external receipt verification.
It serializes only participating writers using the canonical WorkSupplyState
write-lock and requires post-lock Task identity, claim, session and lease checks.
Existing nonparticipating Bolt writers remain an unresolved P0.
"""
from __future__ import annotations

from typing import Any


class LegacyTaskCompletionDenied(RuntimeError):
    """Rollback the write transaction; the legacy caller owns no current claim."""


class LegacyTaskCompletionReplay(LegacyTaskCompletionDenied):
    """An exact terminal replay is returned only AFTER rolling back lock writes."""

    def __init__(self, result: dict[str, Any]):
        super().__init__("read_only_idempotent_completion")
        self.result = result


_LOCK = """
MATCH (w:WorkSupplyState {name:'canonical'})
SET w.legacy_completion_serial=coalesce(w.legacy_completion_serial,0)+1
RETURN count(w) AS matching_rows
"""

_READ = """
MATCH (t:Task {id:$task_id})
WHERE t.claimed_by=$agent_id
  AND t.claim_id=$claim_id
  AND t.claim_id IS NOT NULL
  AND coalesce(t.execution_attempt,0)>0
  AND (t.agent_session_id IS NULL OR t.agent_session_id=$session_id)
  AND t.target_agent_id IS NULL
  AND coalesce(t.machine_authorized,false)=false
  AND t.machine_authorization_generation IS NULL
  AND t.machine_authorization_approval_id IS NULL
  AND t.machine_token_epoch IS NULL
  AND t.projection_generation IS NULL
  AND coalesce(t.hermes_claim_scope,false)=false
  AND NOT toLower(coalesce(t.claimed_by,'')) CONTAINS 'hermes'
  AND NOT EXISTS {
    MATCH (t)-[:DISPATCHED_AS]->(d:Dispatch)
    WHERE d.paperclip_issue_id IS NOT NULL
  }
RETURN t.status AS status, t.lease_expires_at_ts>timestamp() AS lease_fresh
"""


def require_current_legacy_completion(
    tx: Any, *, task_id: str, agent_id: str,
    claim_id: str | None, session_id: str | None,
) -> str:
    if (not isinstance(task_id, str) or not task_id.strip()
        or not isinstance(agent_id, str) or not agent_id.strip()
        or not isinstance(claim_id, str) or not claim_id.strip()
        or (session_id is not None and
            (not isinstance(session_id, str) or not session_id.strip()))):
        raise LegacyTaskCompletionDenied("missing_exact_legacy_claim")

    lock=tx.run(_LOCK).single()
    if lock is None or int(lock.get("matching_rows") or 0)!=1:
        raise LegacyTaskCompletionDenied("canonical_task_writer_lock_not_unique")
    rec=tx.run(_READ, {
        "task_id":task_id, "agent_id":agent_id,
        "claim_id":claim_id, "session_id":session_id,
    }).single()
    if rec is None:
        raise LegacyTaskCompletionDenied("protected_or_stale_legacy_claim")
    state=rec["status"]
    if state in ("CLAIMED","RUNNING"):
        if rec["lease_fresh"] is not True:
            raise LegacyTaskCompletionDenied("expired_legacy_lease")
    elif state not in ("DONE","FAILED","CANCELLED"):
        raise LegacyTaskCompletionDenied("inactive_legacy_claim")
    return state
