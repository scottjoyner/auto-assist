"""Research-only opt-in legacy Task heartbeat and anonymous status quarantine.

The canonical WorkSupplyState lock serializes only *participating* writers.
This module cannot supply machine admission, owner authentication, independent
signatures, external effect receipts, or complete direct-Bolt write authority.

Unattributed update_task_status has no actor/claim/session context to
authenticate. Strict mode therefore refuses ALL such writes rather than
pretending that an unprotected Task is an authorized Task.
"""
from __future__ import annotations

import json
from typing import Any


class LegacyWriterDenied(RuntimeError):
    """A protected/stale/unattributed legacy mutation was rejected."""


_LOCK = """
MATCH (w:WorkSupplyState {name:'canonical'})
SET w.legacy_heartbeat_serial=coalesce(w.legacy_heartbeat_serial,0)+1
RETURN count(w) AS matching_rows
"""

_HEARTBEAT = """
MATCH (t0:Task {id:$task_id})
WITH collect(t0) AS matches
WHERE size(matches)=1
UNWIND matches AS t
WITH t
WHERE t.claimed_by=$agent_id
  AND t.claim_id=$claim_id
  AND t.claim_id IS NOT NULL
  AND coalesce(t.execution_attempt,0)>0
  AND (t.agent_session_id IS NULL OR t.agent_session_id=$session_id)
  AND t.status IN ['CLAIMED','RUNNING','PAUSING']
  AND t.lease_expires_at_ts>timestamp()
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
WITH t,
  CASE
    WHEN $lease_seconds IS NOT NULL THEN $lease_seconds
    WHEN coalesce(t.lease_seconds,900)<1 THEN 1
    WHEN coalesce(t.lease_seconds,900)>900 THEN 900
    ELSE coalesce(t.lease_seconds,900)
  END AS lease
SET t.heartbeat_by=$agent_id,
    t.heartbeat_metadata_json=$metadata_json,
    t.status=CASE
      WHEN t.status='PAUSING' THEN 'PAUSING'
      WHEN $status IS NULL THEN t.status
      ELSE $status
    END,
    t.lease_seconds=lease,
    t.last_heartbeat_at=datetime(),
    t.last_heartbeat_at_ts=timestamp(),
    t.lease_expires_at_ts=timestamp()+lease*1000,
    t.updated_at=datetime(),
    t.updated_at_ts=timestamp()
RETURN t
"""


def _heartbeat_transaction(tx: Any, params: dict[str, Any]) -> dict[str, Any]:
    row = tx.run(_LOCK).single()
    if row is None or int(row.get("matching_rows") or 0) != 1:
        raise LegacyWriterDenied("canonical_task_writer_lock_not_unique")
    rec = tx.run(_HEARTBEAT, params).single()
    if rec is None:
        raise LegacyWriterDenied("protected_or_stale_legacy_heartbeat")
    return dict(rec["t"])


def strict_legacy_heartbeat(
    neo: Any, *,
    task_id: str, agent_id: str, claim_id: str | None,
    session_id: str | None, status: str | None,
    lease_seconds: int | None, metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Require exact current legacy ownership, then heartbeat within one tx.

    Returns None for a denied heartbeat, matching the existing client API.
    Strict mode does not modify Task agent_session_id; a claimed session is
    immutable through heartbeats. It also prohibits terminal status through
    heartbeat and bounds lease extension.
    """
    if (
        not isinstance(task_id,str) or not task_id.strip()
        or not isinstance(agent_id,str) or not agent_id.strip()
        or not isinstance(claim_id,str) or not claim_id.strip()
        or (session_id is not None and
            (not isinstance(session_id,str) or not session_id.strip()))
        or status not in (None,"RUNNING")
        or (lease_seconds is not None and (
            type(lease_seconds) is not int or not 1<=lease_seconds<=900))
        or metadata is not None and not isinstance(metadata,dict)
    ):
        return None
    try:
        metadata_json = json.dumps(metadata or {}, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        return None
    if len(metadata_json)>4096:
        return None
    params={
        "task_id":task_id,"agent_id":agent_id,"claim_id":claim_id,
        "session_id":session_id,"status":status,
        "lease_seconds":lease_seconds,"metadata_json":metadata_json,
    }
    try:
        with neo._session() as session:
            return session.execute_write(_heartbeat_transaction,params)
    except LegacyWriterDenied:
        return None


def reject_unattributed_status_change(task_id: str, status: str) -> None:
    """No authenticated actor/claim/lease exists for this legacy API shape.

    Quarantine *all* direct Task status writes in strict research mode.
    Ordinary and protected Tasks require distinct, authenticated protocols.
    """
    raise LegacyWriterDenied("unattributed_direct_Task_status_writer")
