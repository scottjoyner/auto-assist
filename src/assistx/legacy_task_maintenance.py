"""Research-only maintenance Task-writer quarantine, fail-closed by design.

Not a general Task ownership protocol. Both maintenance Task writers must
acquire the canonical WorkSupplyState write lock in an explicit transaction
and re-read Task state/provenance in a *subsequent* Cypher statement.

The remaining legacy Task writers, arbitrary Bolt principals, and the absence
of independent graph custody keep production NO-GO. This must not be deployed
before a full writer census and operational review of skipped Task retention.
"""
from __future__ import annotations

from typing import Any


class MaintenanceTaskWriteBlocked(RuntimeError):
    """Canonical writer coordination is missing/ambiguous: rollback any writes."""


_LOCK = """
MATCH (w:WorkSupplyState {name:'canonical'})
SET w.legacy_maintenance_serial=coalesce(w.legacy_maintenance_serial,0)+1
RETURN count(w) AS matching_rows
"""

# Reaper must preserve original stale lease behavior but never recycle a Task
# with any scoped-machine authorization, explicit node target, claim generation
# or Hermes provenance. The post-lock read is separate from _LOCK to avoid a
# stale pre-lock snapshot being used to reset a newly claimed Task.
_REAP = """
MATCH (t:Task)
WHERE toUpper(coalesce(t.status,'')) IN ['CLAIMED','RUNNING','CLAIMED_PASSIVE']
  AND coalesce(t.passive_claim_expires_at_ts,
               t.lease_expires_at_ts,
               coalesce(t.last_heartbeat_ts,t.last_heartbeat_at_ts,t.claimed_at_ts,0)
                 + $lease*1000,0) < timestamp()
  AND t.target_agent_id IS NULL
  AND coalesce(t.machine_authorized,false)=false
  AND t.machine_authorization_generation IS NULL
  AND t.machine_authorization_approval_id IS NULL
  AND t.machine_token_epoch IS NULL
  AND t.projection_generation IS NULL
  AND coalesce(t.hermes_claim_scope,false)=false
  AND NOT toLower(coalesce(t.claimed_by,'')) CONTAINS 'hermes'
  AND NOT toLower(coalesce(t.passive_claim_agent_id,'')) CONTAINS 'hermes'
WITH DISTINCT t
LIMIT 2000
SET t.status='READY',
    t.claimed_by=null,
    t.claimed_at_ts=null,
    t.lease_expires_at_ts=null,
    t.last_heartbeat_ts=null,
    t.last_passive_claim_result='expired_stale_reaped'
REMOVE t.passive_claim_id,
       t.passive_claim_agent_id,
       t.passive_claim_lease_id,
       t.passive_claim_mode,
       t.passive_claimed_at,
       t.passive_claimed_at_ts,
       t.passive_claim_expires_at_ts
RETURN count(t) AS affected
"""

# Deleting a terminal Task also destroys owner/claim/attempt evidence.
# Retain all protected/signed/targeted Tasks, not merely active ones.
_RETENTION = """
MATCH (t:Task)
WHERE t.status IN ['DONE','FAILED','CANCELLED']
  AND coalesce(t.updated_at_ts,t.completed_at_ts,t.created_at_ts,0)
      < timestamp()-($days*24*60*60*1000)
  AND t.target_agent_id IS NULL
  AND coalesce(t.machine_authorized,false)=false
  AND t.machine_authorization_generation IS NULL
  AND t.machine_authorization_approval_id IS NULL
  AND t.machine_token_epoch IS NULL
  AND t.projection_generation IS NULL
  AND coalesce(t.hermes_claim_scope,false)=false
  AND NOT toLower(coalesce(t.claimed_by,'')) CONTAINS 'hermes'
  AND NOT toLower(coalesce(t.completed_by,'')) CONTAINS 'hermes'
  AND NOT toLower(coalesce(t.passive_claim_agent_id,'')) CONTAINS 'hermes'
WITH DISTINCT t
LIMIT 5000
DETACH DELETE t
RETURN count(*) AS affected
"""


# Preserve *all* Task-linked memory, not just records with current
# machine flags. A missing/malformed marker is not evidence that outcome
# history may be deleted. External custody and indirect links remain HOLD.
_MEMORY_RETENTION = """
MATCH (m:MemoryItem)
WHERE coalesce(m.updated_at_ts,m.created_at_ts,0)
      < timestamp()-($days*24*60*60*1000)
  AND NOT EXISTS {
    MATCH (:Task)-[:RELATED_MEMORY]->(m)
  }
WITH DISTINCT m
LIMIT 5000
DETACH DELETE m
RETURN count(*) AS affected
"""


def _writer_transaction(tx: Any, query: str, params: dict[str, Any]) -> int:
    row = tx.run(_LOCK).single()
    if row is None or int(row.get("matching_rows") or 0) != 1:
        raise MaintenanceTaskWriteBlocked("canonical_task_writer_lock_not_unique")
    rec = tx.run(query, params).single()
    return int(rec["affected"]) if rec else 0


def _run(neo: Any, query: str, params: dict[str, Any]) -> int:
    with neo._session() as session:
        return session.execute_write(_writer_transaction, query, params)


def reap_stale_legacy_tasks(neo: Any, *, lease_seconds: int) -> int:
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
        raise MaintenanceTaskWriteBlocked("invalid_stale_lease_seconds")
    return _run(neo, _REAP, {"lease": lease_seconds})


def retain_terminal_legacy_tasks(neo: Any, *, retention_days: int) -> int:
    if type(retention_days) is not int or not 1 <= retention_days <= 36500:
        raise MaintenanceTaskWriteBlocked("invalid_task_retention_days")
    return _run(neo, _RETENTION, {"days": retention_days})


def retain_unlinked_legacy_memory_items(neo: Any, *, retention_days: int) -> int:
    """Only old, Task-unlinked MemoryItems may be garbage-collected.

    MemoryItems linked to ANY Task are retained until an independently
    approved, auditable Task/evidence retention protocol exists.
    """
    if type(retention_days) is not int or not 1 <= retention_days <= 36500:
        raise MaintenanceTaskWriteBlocked("invalid_memory_retention_days")
    return _run(neo, _MEMORY_RETENTION, {"days": retention_days})
