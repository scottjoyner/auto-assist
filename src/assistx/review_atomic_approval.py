"""Research-only atomic intent-review acceptance with HELD (non-dispatchable) outbox.

Source-only. No API, CLI, job or producer calls this. The "actor" is not
authenticated here; no operator role, owner policy or signed fleet admission
is established. NEVER enable this path as a production dispatch shortcut.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


class ReviewAtomicApprovalDenied(RuntimeError):
    """The entire transaction must be rolled back, including lock increments."""


_LOCK = """
MATCH (w:WorkSupplyState {name:'canonical'})
SET w.review_outbox_serial=coalesce(w.review_outbox_serial,0)+1
RETURN count(w) AS matching_rows
"""

# A separate post-lock read (not the pre-lock HTTP Task snapshot).
_READ_REVIEW = """
MATCH (r0:Task {id:$review_id})
WITH collect(r0) AS matches
WHERE size(matches)=1
UNWIND matches AS r
WITH r
WHERE r.kind='intent_review'
  AND r.status='REVIEW'
  AND r.updated_at_ts=$expected_revision
  AND r.review_decision IS NULL
  AND r.reviewed_by IS NULL
  AND r.reviewed_at_ts IS NULL
  AND r.claim_id IS NULL
  AND r.claimed_by IS NULL
  AND r.agent_session_id IS NULL
  AND r.lease_expires_at_ts IS NULL
  AND coalesce(r.execution_attempt,0)=0
  AND r.target_agent_id IS NULL
  AND coalesce(r.machine_authorized,false)=false
  AND r.machine_authorization_generation IS NULL
  AND r.machine_authorization_approval_id IS NULL
  AND r.machine_token_epoch IS NULL
  AND r.projection_generation IS NULL
  AND coalesce(r.hermes_claim_scope,false)=false
  AND coalesce(r.auto_dispatch,false)=false
  AND NOT EXISTS {
    MATCH (r)-[:APPROVED_AS]->(:Task)
  }
  AND NOT EXISTS {
    MATCH (:Task {id:$approved_id})
  }
  AND NOT EXISTS {
    MATCH (:Task {approved_from_review_task:$review_id})
  }
  AND NOT EXISTS {
    MATCH (:ReviewApprovalOutbox {id:$outbox_id})
  }
RETURN elementId(r) AS review_element_id,
       r.title AS title, r.payload_json AS payload_json,
       r.priority AS priority
"""

# Must not emit work: the child is PENDING_ADMISSION rather than READY,
# and the outbox is HELD_UNTIL_ADMISSION, not PENDING_DISPATCH.
_ATOMIC_CREATE = """
MATCH (r:Task)
WHERE elementId(r)=$review_element_id
  AND r.id=$review_id
  AND r.kind='intent_review'
  AND r.status='REVIEW'
  AND r.updated_at_ts=$expected_revision
  AND r.review_decision IS NULL
  AND NOT EXISTS { MATCH (r)-[:APPROVED_AS]->(:Task) }
CREATE (t:Task {
  id:$approved_id,
  kind:'approved_intent',
  type:'task',
  status:'PENDING_ADMISSION',
  title:$title,
  priority:$priority,
  approved_from_review_task:$review_id,
  operator_review_actor:$actor,
  admission_verified:false,
  auto_dispatch:false,
  created_at:datetime(),
  created_at_ts:timestamp(),
  updated_at:datetime(),
  updated_at_ts:timestamp()
})
CREATE (o:ReviewApprovalOutbox {
  id:$outbox_id,
  event_type:'review_approved_candidate',
  status:'HELD_UNTIL_ADMISSION',
  review_task_id:$review_id,
  candidate_task_id:$approved_id,
  verified_operator:false,
  signed_admission:false,
  created_at:datetime(),
  created_at_ts:timestamp()
})
CREATE (r)-[:APPROVED_AS]->(t)
CREATE (r)-[:HAS_APPROVAL_OUTBOX]->(o)
SET r.status='DONE',
    r.review_decision='approved_held',
    r.review_note=$note,
    r.reviewed_by=$actor,
    r.reviewed_at=datetime(),
    r.reviewed_at_ts=timestamp(),
    r.updated_at=datetime(),
    r.updated_at_ts=timestamp()
RETURN r.id AS review_task_id,
       t.id AS candidate_task_id,
       t.status AS candidate_status,
       o.id AS outbox_id,
       o.status AS outbox_status,
       r.review_decision AS decision
"""


def _safe_source_title(row: Any) -> str:
    raw = row.get("payload_json")
    if raw is None:
        payload = {}
    else:
        if not isinstance(raw, str) or len(raw) > 65536:
            raise ReviewAtomicApprovalDenied("untrusted_review_payload_shape")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ReviewAtomicApprovalDenied("malformed_review_payload") from exc
        if not isinstance(payload, dict):
            raise ReviewAtomicApprovalDenied("untrusted_review_payload_shape")
    if "source_text" in payload:
        source = payload["source_text"]
    else:
        source = row.get("title") or ""
    if not isinstance(source, str) or not source.strip():
        raise ReviewAtomicApprovalDenied("missing_review_title")
    return source[:120]


def _tx_approve(tx: Any, params: dict[str, Any]) -> dict[str, Any]:
    lock = tx.run(_LOCK).single()
    if lock is None or int(lock.get("matching_rows") or 0) != 1:
        raise ReviewAtomicApprovalDenied("canonical_writer_lock_not_unique")
    row = tx.run(_READ_REVIEW, params).single()
    if row is None:
        raise ReviewAtomicApprovalDenied("stale_protected_or_duplicate_review")
    params = {
        **params,
        "review_element_id":row["review_element_id"],
        "title":_safe_source_title(row),
        "priority":row.get("priority") or "MEDIUM",
    }
    committed = tx.run(_ATOMIC_CREATE, params).single()
    if committed is None:
        raise ReviewAtomicApprovalDenied("review_changed_before_atomic_create")
    return dict(committed)


def propose_held_review_approval(
    neo: Any, *, review_id: str, actor: str,
    expected_revision: int, note: str = "",
    auto_dispatch: bool = False,
) -> dict[str, Any]:
    """Create an inert child, held outbox, and audit review in one transaction.

    The actor is caller-asserted and NOT an authenticated principal. No READY
    Task, auto-assignment, context packet, Paperclip call or external effect.
    """
    if (not isinstance(review_id, str) or not review_id.strip() or len(review_id) > 256
        or not isinstance(actor, str) or not actor.strip() or len(actor) > 128
        or type(expected_revision) is not int or expected_revision <= 0
        or not isinstance(note, str) or len(note) > 1000
        or auto_dispatch is not False):
        raise ReviewAtomicApprovalDenied("invalid_or_dispatching_review_request")
    digest = hashlib.sha256(review_id.encode("utf-8")).hexdigest()
    params = {
        "review_id":review_id,
        "actor":actor,
        "expected_revision":expected_revision,
        "note":note,
        "approved_id":"held-review-task-"+digest,
        "outbox_id":"held-review-outbox-"+digest,
    }
    with neo._session() as session:
        return session.execute_write(_tx_approve, params)
