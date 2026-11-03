"""Source-only, disabled machine Task CLAIM transaction contract.

This module is intentionally not exposed through FastAPI. An authenticated
machine principal, signed runtime identity, session ownership and all side-
effect writers must be independently reviewed before any endpoint can call it.
Never delegate to the legacy Neo4jClient.claim_task() wildcard claim path.
"""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


_NODE = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')
_SESSION = re.compile(r'[A-Za-z0-9_.:-]{8,160}\Z')
_TASK = re.compile(r'[A-Za-z0-9_.:-]{1,256}\Z')
_ARTIFACT = re.compile(r'[0-9a-f]{64}\Z')
_MAX_WORK_AGE_MS = 90_000
_MAX_LEASE_SECONDS = 300


class ScopedClaimDenied(RuntimeError):
    """No claim; transaction must roll back all attempted serial-lock writes."""


@dataclass(frozen=True)
class ScopedClaimRequest:
    node_id: str
    task_id: str
    session_id: str
    projection_generation: int
    artifact_fingerprint: str
    token_epoch: int
    lease_seconds: int = 120

    def validate(self) -> None:
        if (not _NODE.fullmatch(self.node_id)
                or not _TASK.fullmatch(self.task_id)
                or not _SESSION.fullmatch(self.session_id)
                or not _ARTIFACT.fullmatch(self.artifact_fingerprint)
                or type(self.projection_generation) is not int
                or self.projection_generation <= 0
                or type(self.token_epoch) is not int or self.token_epoch <= 0
                or type(self.lease_seconds) is not int
                or not 1 <= self.lease_seconds <= _MAX_LEASE_SECONDS):
            raise ValueError('invalid scoped Task claim contract')


# Write-lock the single WorkSupplyState row BEFORE reading Task state. This
# serializes all future scoped-machine claims on one capacity authority. The
# lock counter is rolled back with a failed claim transaction. Legacy Task
# writers currently do not participate, hence this function is UNDEPLOYABLE
# until the source-owner census and all competing claim writers are fenced.
_SERIALIZE_CAPACITY = """
MATCH (w:WorkSupplyState {name:'canonical'})
WHERE w.projection_generation=$generation
  AND w.generated_at_ts>timestamp()-$freshness
  AND w.allow_background=true
  AND coalesce(w.free_slots,0)>0
  AND coalesce(w.total_slots,0)>0
SET w.scoped_claim_serial=coalesce(w.scoped_claim_serial,0)+1
RETURN w.total_slots AS total_slots, w.scoped_claim_serial AS serial
"""

# The Task mutation and all independent authorities are in one Neo4j write
# transaction, after acquiring the capacity row's write lock. No legacy
# `target_agent_id IS NULL`, `size(capabilities)=0`, or unclaimed attempt=0
# shortcut is permitted. The graph must have explicit, custodian-approved
# MachinePrincipalAdmission and Task.machine_authorized evidence; production
# currently has none of it and is intentionally guaranteed to deny.
_CLAIM_EXACT_TARGET = """
MATCH (p:FleetProjectionState {name:'canonical', status:'approved'})
WHERE p.generation=$generation AND p.expires_at_ts>timestamp()
  AND p.approved_by IS NOT NULL AND p.approval_id IS NOT NULL
  AND p.manifest_checksum IS NOT NULL
MATCH (w:WorkSupplyState {name:'canonical'})
WHERE w.projection_generation=$generation
  AND w.generated_at_ts>timestamp()-$freshness
  AND w.allow_background=true
  AND coalesce(w.total_slots,0)>0 AND coalesce(w.free_slots,0)>0
MATCH (auth:MachinePrincipalAdmission {node_id:$node_id, status:'approved'})
WHERE auth.projection_generation=$generation
  AND auth.token_epoch=$token_epoch
  AND auth.artifact_fingerprint=$artifact
  AND auth.approved_by IS NOT NULL AND auth.approval_id IS NOT NULL
  AND auth.expires_at_ts>timestamp()
MATCH (r:RuntimeInstance {node_id:$node_id})-[:SERVES]->(m:LoadedModelInstance)
WHERE r.admitted=true AND r.expires_at_ts>timestamp()
  AND toLower(r.status) IN ['online','healthy','ready']
  AND m.admitted=true AND m.expires_at_ts>timestamp()
  AND m.artifact_fingerprint=$artifact
  AND m.machine_chat_eligible=true
  AND m.model_instance_id IS NOT NULL
MATCH (a:AccessPath {runtime_instance_id:r.runtime_instance_id})
WHERE a.approved=true AND a.expires_at_ts>timestamp()
  AND a.approved_by IS NOT NULL AND a.approval_id IS NOT NULL
MATCH (c:CapacityObservation {runtime_instance_id:r.runtime_instance_id})
WHERE c.approved=true AND c.expires_at_ts>timestamp()
  AND c.parallel_slots>0
  AND c.approved_by IS NOT NULL AND c.approval_id IS NOT NULL
MATCH (t:Task {id:$task_id, status:'READY', target_agent_id:$node_id})
WHERE t.required_capabilities=['llm']
  AND t.requires_approval=false AND t.paperclip_dispatched=false
  AND t.machine_authorized=true
  AND t.machine_authorization_generation=$generation
  AND t.machine_authorization_approval_id IS NOT NULL
  AND t.owner_id IS NOT NULL
  AND t.claim_id IS NULL AND t.claimed_by IS NULL
  AND t.agent_session_id IS NULL AND t.lease_expires_at_ts IS NULL
  AND coalesce(t.execution_attempt,0)=0
  AND NOT EXISTS {
    MATCH (t)-[:DISPATCHED_AS]->(d:Dispatch)
    WHERE d.paperclip_issue_id IS NOT NULL
  }
WITH DISTINCT t, w
OPTIONAL MATCH (busy:Task)
WHERE busy.status IN ['CLAIMED','RUNNING','PAUSING']
WITH t, w, count(busy) AS active_tasks
WHERE active_tasks < w.total_slots
SET t.status='CLAIMED', t.claimed_by=$node_id,
    t.agent_session_id=$session_id, t.claim_id=$claim_id,
    t.execution_attempt=1,
    t.projection_generation=$generation,
    t.machine_token_epoch=$token_epoch,
    t.claimed_at_ts=timestamp(),
    t.lease_seconds=$lease_seconds,
    t.lease_expires_at_ts=timestamp()+$lease_seconds*1000,
    t.updated_at_ts=timestamp()
RETURN t.id AS task_id, t.claim_id AS claim_id,
       t.agent_session_id AS session_id,
       t.projection_generation AS generation,
       t.lease_expires_at_ts AS lease_expires_at_ts,
       t.execution_attempt AS execution_attempt
"""


def _attempt_claim(tx: Any, params: dict[str, Any]) -> dict[str, Any]:
    serial = tx.run(_SERIALIZE_CAPACITY, params).single()
    if serial is None:
        raise ScopedClaimDenied('capacity_or_approval_unavailable')
    result = tx.run(_CLAIM_EXACT_TARGET, params).single()
    if result is None:
        # Raising instead of returning false is ESSENTIAL: it rolls back the
        # serial-lock mutation and any intermediate attempt writes.
        raise ScopedClaimDenied('ownership_or_claim_fence_denied')
    return dict(result)


def scoped_claim_task(
    request: ScopedClaimRequest,
    *,
    neo_factory: Callable[[], Any],
    token_authenticated: bool,
    approved_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    """Not routed; no authorization based solely on client-supplied fields.

    `approved_policy` MUST be resolved from a trusted server-owned registry
    after authenticating the exact node identity. All graph authority is
    rechecked in the write transaction to detect rotation and revocation.
    """
    if os.getenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS') != '1':
        return {'claimed': False, 'reason': 'feature_disabled'}
    try:
        request.validate()
    except ValueError:
        return {'claimed': False, 'reason': 'invalid_request'}
    policy = approved_policy if isinstance(approved_policy, dict) else {}
    if (token_authenticated is not True
            or policy.get('enabled') is not True
            or policy.get('node_id') != request.node_id
            or policy.get('audience') != 'targeted-llm-claim'
            or policy.get('allowed_capabilities') != ['llm']
            or type(policy.get('projection_generation')) is not int
            or policy['projection_generation'] != request.projection_generation
            or policy.get('artifact_fingerprint') != request.artifact_fingerprint
            or type(policy.get('token_epoch')) is not int
            or policy['token_epoch'] != request.token_epoch):
        return {'claimed': False, 'reason': 'node_authority_denied'}
    params={
        'node_id': request.node_id,
        'task_id': request.task_id,
        'session_id': request.session_id,
        'artifact': request.artifact_fingerprint,
        'generation': request.projection_generation,
        'token_epoch': request.token_epoch,
        'lease_seconds': request.lease_seconds,
        'freshness': _MAX_WORK_AGE_MS,
        'claim_id': uuid.uuid4().hex,
    }
    neo = neo_factory()
    try:
        with neo._session() as session:
            try:
                result = session.execute_write(_attempt_claim, params)
            except ScopedClaimDenied as exc:
                return {'claimed': False, 'reason': str(exc)}
            return {'claimed': True, 'receipt': result}
    finally:
        neo.close()
