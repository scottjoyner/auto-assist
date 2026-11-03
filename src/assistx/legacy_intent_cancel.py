"""Research-only fail-closed legacy Intent -> Task cancellation fence.

This prototype is NOT a complete writer census, revocation authority or
independent cryptographic custody implementation. It uses the same canonical
WorkSupplyState row-lock discipline as the proposed scoped machine writers.
The lock must precede a second query's Task eligibility evaluation; a
single SET following a stale WHERE is not sufficient under concurrency.

No scoped-machine Task may be cancelled by this legacy path. Approved scoped
Task cancellation needs its own owner-signed, generation-fenced protocol.
Do not deploy while legacy Task writers and physical DB privileges are
unaccounted for.
"""
from __future__ import annotations

from typing import Any


class LegacyIntentCancelBlocked(RuntimeError):
    """The canonical coordination row is unavailable; no Task was cancelled."""


_LOCK = """
MATCH (w:WorkSupplyState {name:'canonical'})
SET w.legacy_intent_cancel_serial=coalesce(w.legacy_intent_cancel_serial, 0)+1
RETURN count(w) AS matching_rows
"""

# This eligibility check MUST be in a *new query* after _LOCK has acquired
# the write lock; all scoped-machine claim/execution transactions also write
# this canonical WorkSupplyState node. Missing authorization properties on
# legacy Tasks are NOT evidence for enrolling them into scoped-machine work.
_CANCEL = """
MATCH (i:Intent {id:$intent_id})-[:CREATED_TASK]->(t:Task)
WHERE t.status IN ['READY','CLAIMED','RUNNING']
  AND coalesce(t.machine_authorized,false)=false
  AND t.machine_authorization_generation IS NULL
  AND t.machine_authorization_approval_id IS NULL
  AND t.machine_token_epoch IS NULL
  AND coalesce(t.hermes_claim_scope,false)=false
WITH DISTINCT t
SET t.status='CANCELLED',
    t.cancelled_reason=$reason,
    t.updated_at=datetime(),
    t.updated_at_ts=timestamp()
RETURN count(t) AS cancelled
"""


def _cancel_transaction(tx: Any, intent_id: str, reason: str) -> int:
    lock = tx.run(_LOCK).single()
    if lock is None or int(lock.get("matching_rows") or 0) != 1:
        # execute_write rolls back the entire transaction. Fail closed even
        # for apparent legacy Tasks: source capacity authority is missing.
        raise LegacyIntentCancelBlocked("canonical_task_writer_lock_not_unique")
    result = tx.run(_CANCEL, {"intent_id": intent_id, "reason": reason}).single()
    return int(result["cancelled"] if result else 0)


def cancel_legacy_intent_tasks(neo: Any, *, intent_id: str, reason: str) -> int:
    """Count legacy Tasks cancelled, excluding all scoped/Hermes Tasks.

    This is not an approval, externally attested effect, general revocation or
    a substitute for a post-lock generation- and owner-bound cancellation CAS.
    Only the two specified Intent cancellation callers are enrolled.
    """
    if not isinstance(intent_id, str) or not intent_id.strip():
        raise LegacyIntentCancelBlocked("invalid_intent_id")
    if not isinstance(reason, str):
        raise LegacyIntentCancelBlocked("invalid_cancel_reason")
    with neo._session() as session:
        return session.execute_write(_cancel_transaction, intent_id, reason[:500])
