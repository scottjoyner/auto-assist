"""Research-only machine heartbeat and accepted-result transaction contracts.

NOT connected to any FastAPI route, worker, or deployed task writer. An HTTP
200 from a model must never be treated as independently accepted Task work.

Both operations are disabled by default, require a trusted server-side policy,
and use the same canonical WorkSupplyState row write-lock discipline as the
research scoped-claim path. This only serializes writers that PARTICIPATE in
that discipline; legacy claims and approval revocation do not yet participate.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .scoped_machine_claim import ScopedClaimRequest


_CLAIM_ID = re.compile(r'[0-9a-f]{32}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_MAX_LEASE = 300
_FRESH_MS = 90_000


class ScopedExecutionDenied(RuntimeError):
    """Trigger a rollback of all preliminary graph writes on a failed fence."""


@dataclass(frozen=True)
class ScopedExecutionRequest:
    node_id: str
    task_id: str
    session_id: str
    claim_id: str
    projection_generation: int
    token_epoch: int
    artifact_fingerprint: str
    lease_seconds: int = 120
    result_sha256: str | None = None
    acceptance_id: str | None = None

    def validate(self, *, finalize: bool) -> None:
        ScopedClaimRequest(
            node_id=self.node_id, task_id=self.task_id,
            session_id=self.session_id,
            projection_generation=self.projection_generation,
            token_epoch=self.token_epoch,
            artifact_fingerprint=self.artifact_fingerprint,
            lease_seconds=self.lease_seconds,
        ).validate()
        if not isinstance(self.claim_id, str) or not _CLAIM_ID.fullmatch(self.claim_id):
            raise ValueError('invalid opaque claim identity')
        if finalize:
            if (not isinstance(self.result_sha256, str)
                    or not _DIGEST.fullmatch(self.result_sha256)
                    or not isinstance(self.acceptance_id, str)
                    or not _CLAIM_ID.fullmatch(self.acceptance_id)):
                raise ValueError('accepted result identifiers required')
        elif self.result_sha256 is not None or self.acceptance_id is not None:
            raise ValueError('heartbeat cannot carry a result or acceptance')


def _policy_permits(request: ScopedExecutionRequest, policy: dict[str, Any] | None) -> bool:
    if not isinstance(policy, dict):
        return False
    return (policy.get('enabled') is True
            and policy.get('node_id') == request.node_id
            and policy.get('audience') == 'targeted-llm-execution'
            and policy.get('allowed_capabilities') == ['llm']
            and type(policy.get('projection_generation')) is int
            and policy['projection_generation'] == request.projection_generation
            and type(policy.get('token_epoch')) is int
            and policy['token_epoch'] == request.token_epoch
            and policy.get('artifact_fingerprint') == request.artifact_fingerprint)


# Always take the *same row lock* as scoped_machine_claim._SERIALIZE_CAPACITY.
# This serializes these research callables among themselves and scoped claims.
# It is NOT a valid global capacity lock until every legacy/revocation writer
# participates. This UPDATE is rolled back on denial (not a completed claim).
_LOCK = """
MATCH (w:WorkSupplyState {name:'canonical'})
WHERE w.projection_generation=$generation
  AND w.generated_at_ts>timestamp()-$freshness
  AND w.allow_background=true
  AND coalesce(w.total_slots,0)>0
SET w.scoped_execution_serial=coalesce(w.scoped_execution_serial,0)+1
RETURN w.scoped_execution_serial AS serial
"""

# An expiry or revocation between the API/agent call and this actual write
# transaction must deny the transition; only exact same-node/session/claim,
# generation/epoch/artifact and still-valid authority can proceed.
_COMMON = """
MATCH (p:FleetProjectionState {name:'canonical',status:'approved'})
WHERE p.generation=$generation AND p.expires_at_ts>timestamp()
  AND p.approved_by IS NOT NULL AND p.approval_id IS NOT NULL
  AND p.manifest_checksum IS NOT NULL
MATCH (w:WorkSupplyState {name:'canonical'})
WHERE w.projection_generation=$generation
  AND w.generated_at_ts>timestamp()-$freshness
  AND w.allow_background=true
  AND coalesce(w.total_slots,0)>0
MATCH (auth:MachinePrincipalAdmission {node_id:$node_id,status:'approved'})
WHERE auth.projection_generation=$generation
  AND auth.token_epoch=$token_epoch
  AND auth.artifact_fingerprint=$artifact
  AND auth.approved_by IS NOT NULL AND auth.approval_id IS NOT NULL
  AND auth.expires_at_ts>timestamp()
MATCH (r:RuntimeInstance {node_id:$node_id})-[:SERVES]->(m:LoadedModelInstance)
WHERE r.admitted=true AND r.expires_at_ts>timestamp()
  AND toLower(r.status) IN ['online','healthy','ready']
  AND m.admitted=true AND m.expires_at_ts>timestamp()
  AND m.machine_chat_eligible=true
  AND m.artifact_fingerprint=$artifact
  AND m.model_instance_id IS NOT NULL
MATCH (a:AccessPath {runtime_instance_id:r.runtime_instance_id})
WHERE a.approved=true AND a.expires_at_ts>timestamp()
  AND a.approved_by IS NOT NULL AND a.approval_id IS NOT NULL
MATCH (c:CapacityObservation {runtime_instance_id:r.runtime_instance_id})
WHERE c.approved=true AND c.expires_at_ts>timestamp()
  AND c.parallel_slots>0 AND c.approved_by IS NOT NULL
  AND c.approval_id IS NOT NULL
MATCH (t:Task {id:$task_id,target_agent_id:$node_id})
WHERE t.status IN ['CLAIMED','RUNNING']
  AND t.claimed_by=$node_id AND t.agent_session_id=$session_id
  AND t.claim_id=$claim_id AND t.execution_attempt=1
  AND t.projection_generation=$generation
  AND t.machine_token_epoch=$token_epoch
  AND t.required_capabilities=['llm']
  AND t.machine_authorized=true
  AND t.machine_authorization_generation=$generation
  AND t.machine_authorization_approval_id=auth.approval_id
  AND t.requires_approval=false AND t.paperclip_dispatched=false
  AND t.owner_id IS NOT NULL
  AND t.lease_expires_at_ts>timestamp()
  AND NOT EXISTS {
    MATCH (t)-[:HAS_ALLOCATION]->(other:AllocationReservation {status:'ACTIVE'})
    WHERE other.expires_at_ts>timestamp() AND other.node_id<>$node_id
  }
  AND NOT EXISTS {
    MATCH (t)-[:DISPATCHED_AS]->(d:Dispatch)
    WHERE d.paperclip_issue_id IS NOT NULL
  }
WITH DISTINCT t
"""

_HEARTBEAT = _COMMON + """
SET t.status='RUNNING',
    t.last_heartbeat_at_ts=timestamp(),
    t.lease_seconds=$lease_seconds,
    t.lease_expires_at_ts=timestamp()+$lease_seconds*1000,
    t.updated_at_ts=timestamp()
RETURN t.id AS task_id,t.claim_id AS claim_id,
       t.agent_session_id AS session_id,
       t.lease_expires_at_ts AS lease_expires_at_ts,
       t.execution_attempt AS execution_attempt
"""

# A merely generated answer or producer-supplied SHA is not accepted work.
# This requires a PREEXISTING external-custodian acceptance record bound to
# the precise attempt and digest, and consumes it atomically with Task DONE.
# The provenance service, independent signature and retention are NOT
# implemented in this module. Current production has no such authority.
_FINALIZE = _COMMON + """
MATCH (acceptance:TaskResultAcceptance {
  id:$acceptance_id,status:'accepted',task_id:$task_id,
  claim_id:$claim_id,session_id:$session_id,node_id:$node_id
})
WHERE acceptance.projection_generation=$generation
  AND acceptance.token_epoch=$token_epoch
  AND acceptance.artifact_fingerprint=$artifact
  AND acceptance.result_sha256=$result_sha256
  AND acceptance.signature_verified=true
  AND acceptance.external_anchor_verified=true
  AND acceptance.external_checkpoint_hash IS NOT NULL
  AND size(acceptance.external_checkpoint_hash)=64
  AND acceptance.accepted_at_ts>=t.claimed_at_ts
  AND acceptance.accepted_at_ts<=timestamp()
  AND acceptance.approved_by IS NOT NULL
  AND acceptance.approved_by<>$node_id
  AND acceptance.approval_id IS NOT NULL
  AND acceptance.expires_at_ts>timestamp()
  AND acceptance.consumed=false
WITH t,collect(DISTINCT acceptance) AS qualified_acceptances
WHERE size(qualified_acceptances)=1
WITH t,qualified_acceptances[0] AS acceptance
SET t.status='DONE',t.completed_by=$node_id,
    t.result_sha256=$result_sha256,
    t.completion_acceptance_id=$acceptance_id,
    t.completed_at_ts=timestamp(),t.updated_at_ts=timestamp(),
    acceptance.consumed=true,acceptance.consumed_at_ts=timestamp()
RETURN t.id AS task_id,t.status AS status,
       t.claim_id AS claim_id,
       t.completion_acceptance_id AS acceptance_id,
       t.result_sha256 AS result_sha256
"""


def _execute(tx: Any, params: dict[str, Any], *, finalize: bool) -> dict[str, Any]:
    if tx.run(_LOCK, params).single() is None:
        raise ScopedExecutionDenied('capacity_or_approval_unavailable')
    receipt = tx.run(_FINALIZE if finalize else _HEARTBEAT, params).single()
    if receipt is None:
        # Required to roll back `_LOCK` and any intermediate graph mutation.
        raise ScopedExecutionDenied('attempt_or_acceptance_fence_denied')
    return dict(receipt)


def scoped_execution_transition(
    request: ScopedExecutionRequest,
    *,
    finalize: bool,
    token_authenticated: bool,
    approved_policy: dict[str, Any] | None,
    neo_factory: Callable[[], Any],
) -> dict[str, Any]:
    """No API registration; caller cannot forge a signed admission via params."""
    if os.getenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION') != '1':
        return {'transition_applied': False, 'reason': 'feature_disabled'}
    try:
        request.validate(finalize=finalize)
    except ValueError:
        return {'transition_applied': False, 'reason': 'invalid_attempt'}
    if token_authenticated is not True or not _policy_permits(request, approved_policy):
        return {'transition_applied': False, 'reason': 'node_authority_denied'}
    params = {
        'node_id': request.node_id,
        'task_id': request.task_id,
        'session_id': request.session_id,
        'claim_id': request.claim_id,
        'generation': request.projection_generation,
        'token_epoch': request.token_epoch,
        'artifact': request.artifact_fingerprint,
        'lease_seconds': request.lease_seconds,
        'result_sha256': request.result_sha256,
        'acceptance_id': request.acceptance_id,
        'freshness': _FRESH_MS,
    }
    neo = neo_factory()
    try:
        with neo._session() as session:
            try:
                receipt = session.execute_write(
                    lambda tx: _execute(tx, params, finalize=finalize))
            except ScopedExecutionDenied as exc:
                return {'transition_applied': False, 'reason': str(exc)}
            return {'transition_applied': True, 'receipt': receipt,
                    'graph_acceptance_predicates_checked': finalize,
                    'external_custody_verified_by_this_module': False}
    finally:
        neo.close()
