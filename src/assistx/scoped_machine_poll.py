"""Read-only, fail-closed machine Task discovery (NOT claim/admission authority).

Nothing in this module permits an agent to claim, complete, or view a Task
payload. Node tokens are not general operator credentials. The existing Basic
Task API is deliberately unchanged and this route is disabled by default.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Path, Query, Response

from .node_identity import verify_node_token
from .runtime_projection import RuntimeProjectionBlocked, _projection_state


_TASK_AUDIENCE = 'targeted-llm-observer'
_NODE_RE = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')
_FINGERPRINT_RE = re.compile(r'[0-9a-f]{64}\Z')
_MAX_STATE_AGE_MS = 90_000


def _enabled() -> bool:
    return os.getenv('ASSISTX_ENABLE_SCOPED_NODE_OBSERVATION') == '1'


def _node_policy(registry: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    entry = registry.get(node_id)
    if not isinstance(entry, dict):
        return None
    fingerprint = entry.get('artifact_fingerprint')
    if (entry.get('enabled') is True
            and entry.get('audience') == _TASK_AUDIENCE
            and entry.get('allowed_capabilities') == ['llm']
            and type(entry.get('projection_generation')) is int
            and entry['projection_generation'] > 0
            and isinstance(fingerprint, str)
            and _FINGERPRINT_RE.fullmatch(fingerprint)):
        return entry
    return None



def _has_chat_role(encoded_roles: Any) -> bool:
    """Require a well-formed JSON list proving chat/llm on a current model."""
    if not isinstance(encoded_roles, list):
        return False
    for encoded in encoded_roles:
        try:
            capabilities = json.loads(encoded) if isinstance(encoded, str) else encoded
        except (ValueError, TypeError):
            continue
        if isinstance(capabilities, list) and any(
            role in ("chat", "llm") for role in capabilities
            if isinstance(role, str)
        ):
            return True
    return False


def candidate_tasks(
    node_id: str,
    *,
    neo_factory: Callable[[], Any],
    limit: int,
    expected_generation: int,
    expected_artifact: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """No Task payload or claim authority; read-only exact-target hints only.

    Independently read the current approved projection *state*, never sign or
    mint a new projection. Reject stale/empty work supply and missing physical
    runtime, model, access-path or capacity witnesses. Claims remain disabled.
    """
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    try:
        canonical = _projection_state(neo_factory, now)
        generation = canonical['generation']
        if generation != expected_generation:
            raise RuntimeProjectionBlocked('node policy generation is stale')
    except (RuntimeProjectionBlocked, ValueError, TypeError):
        raise HTTPException(status_code=503, detail='runtime_authority_unavailable') from None

    neo = neo_factory()
    try:
        with neo._session() as session:
            supply = session.run(
                """
                MATCH (s:WorkSupplyState {name:'canonical'})
                WHERE s.projection_generation=$generation
                  AND s.generated_at_ts > $cutoff
                  AND coalesce(s.total_slots, 0) > 0
                  AND coalesce(s.free_slots, 0) > 0
                  AND s.allow_background = true
                RETURN s.total_slots AS slots LIMIT 1
                """,
                {'generation': generation, 'cutoff': now - _MAX_STATE_AGE_MS},
            ).single()
            if not supply or type(supply['slots']) is not int or supply['slots'] <= 0:
                raise HTTPException(status_code=503, detail='runtime_capacity_unavailable')
            witness = session.run(
                """
                MATCH (r:RuntimeInstance {node_id:$node_id})
                WHERE coalesce(r.admitted,false)=true
                  AND toLower(coalesce(r.status,'unknown')) IN ['online','healthy','ready']
                  AND coalesce(r.expires_at_ts,0)>$now
                MATCH (r)-[:SERVES]->(m:LoadedModelInstance)
                WHERE coalesce(m.admitted,false)=true
                  AND coalesce(m.expires_at_ts,0)>$now
                MATCH (a:AccessPath {runtime_instance_id:r.runtime_instance_id})
                WHERE coalesce(a.approved,false)=true
                  AND coalesce(a.expires_at_ts,0)>$now
                  AND a.approved_by IS NOT NULL AND a.approval_id IS NOT NULL
                MATCH (c:CapacityObservation {runtime_instance_id:r.runtime_instance_id})
                WHERE coalesce(c.approved,false)=true
                  AND coalesce(c.expires_at_ts,0)>$now
                  AND coalesce(c.parallel_slots,0)>0
                  AND c.approved_by IS NOT NULL AND c.approval_id IS NOT NULL
                  AND m.artifact_fingerprint = $artifact
                  AND m.model_instance_id IS NOT NULL
                RETURN count(DISTINCT r) AS admitted_runtimes,
                       collect(DISTINCT m.capabilities_json) AS model_capabilities
                """,
                {'node_id': node_id, 'now': now, 'artifact': expected_artifact},
            ).single()
            if not witness or int(witness['admitted_runtimes'] or 0) < 1:
                raise HTTPException(status_code=503, detail='node_runtime_unavailable')
            # A loaded embedding-only model is not evidence of usable chat.
            if not _has_chat_role(witness.get('model_capabilities')):
                raise HTTPException(status_code=503, detail='node_chat_model_unavailable')
            # Never use generic list_agent_tasks(agent_id=...): it includes all
            # untargeted READY Tasks. Re-check ALL physical admission witnesses
            # with the canonical approval, work supply and exact Task audience
            # in this final read, not in prior separate reads alone. Neo4j's
            # timestamp() is captured for this read instead of reusing the
            # earlier Python now, which could go stale across multiple queries.
            records = session.run(
                """
                MATCH (p:FleetProjectionState {name:'canonical',status:'approved'})
                WHERE p.generation=$generation AND p.expires_at_ts>timestamp()
                  AND p.approved_by IS NOT NULL AND p.approval_id IS NOT NULL
                MATCH (s:WorkSupplyState {name:'canonical'})
                WHERE s.projection_generation=$generation
                  AND s.generated_at_ts>timestamp()-$max_age_ms
                  AND s.allow_background=true
                  AND coalesce(s.total_slots,0)>0
                  AND coalesce(s.free_slots,0)>0
                MATCH (r:RuntimeInstance {node_id:$node_id})
                WHERE coalesce(r.admitted,false)=true
                  AND toLower(coalesce(r.status,'unknown')) IN ['online','healthy','ready']
                  AND coalesce(r.expires_at_ts,0)>timestamp()
                MATCH (r)-[:SERVES]->(m:LoadedModelInstance)
                WHERE coalesce(m.admitted,false)=true
                  AND coalesce(m.expires_at_ts,0)>timestamp()
                  AND m.artifact_fingerprint=$artifact
                  AND m.model_instance_id IS NOT NULL
                MATCH (a:AccessPath {runtime_instance_id:r.runtime_instance_id})
                WHERE coalesce(a.approved,false)=true
                  AND coalesce(a.expires_at_ts,0)>timestamp()
                  AND a.approved_by IS NOT NULL AND a.approval_id IS NOT NULL
                MATCH (c:CapacityObservation {runtime_instance_id:r.runtime_instance_id})
                WHERE coalesce(c.approved,false)=true
                  AND coalesce(c.expires_at_ts,0)>timestamp()
                  AND coalesce(c.parallel_slots,0)>0
                  AND c.approved_by IS NOT NULL AND c.approval_id IS NOT NULL
                MATCH (t:Task {status:'READY', target_agent_id:$node_id})
                WHERE t.requires_approval = false
                  AND t.paperclip_dispatched = false
                  AND t.required_capabilities=['llm']
                RETURN t.id AS id,
                       collect(DISTINCT m.capabilities_json) AS model_capabilities,
                       coalesce(t.created_at_ts,0) AS created_at_ts
                ORDER BY created_at_ts ASC
                LIMIT $limit
                """,
                {'node_id': node_id, 'limit': limit, 'generation': generation,
                 'artifact': expected_artifact, 'max_age_ms': _MAX_STATE_AGE_MS},
            )
            hints = []
            for record in records:
                # The final read can race model-role revocation too. Reject
                # malformed or embedding-only role evidence, not just the
                # earlier observed capabilities.
                if not _has_chat_role(record.get('model_capabilities')):
                    continue
                task_id = record['id']
                if isinstance(task_id, str) and 0 < len(task_id) <= 256:
                    hints.append({'id': task_id, 'requires_capability': 'llm'})
        return {
            'items': hints,
            'count': len(hints),
            'scope': _TASK_AUDIENCE,
            'projection_generation': generation,
            'authorizes_claim': False,
            'authorizes_dispatch': False,
        }
    finally:
        neo.close()


def build_scoped_machine_router(
    neo_factory: Callable[[], Any],
    token_registry: Callable[[], dict[str, Any]],
    policy_registry: Callable[[], dict[str, Any]],
) -> APIRouter:
    router = APIRouter(tags=['fleet-machine-observation'])

    @router.get('/api/fleet/nodes/{node_id}/assigned-task-hints')
    def read_targeted_hints(
        response: Response,
        node_id: str = Path(..., min_length=1, max_length=128),
        limit: int = Query(10, ge=1, le=20),
        x_fleet_node_token: str | None = Header(None),
    ) -> dict[str, Any]:
        if not _enabled():
            raise HTTPException(status_code=404, detail='not_found')
        if not _NODE_RE.fullmatch(node_id):
            raise HTTPException(status_code=403, detail='node_scope_denied')
        error = verify_node_token(token_registry(), node_id, x_fleet_node_token)
        policy = _node_policy(policy_registry(), node_id) if not error else None
        if error or policy is None:
            raise HTTPException(status_code=403, detail='node_scope_denied')
        response.headers['Cache-Control'] = 'private, no-store'
        return candidate_tasks(
            node_id, neo_factory=neo_factory, limit=limit,
            expected_generation=policy['projection_generation'],
            expected_artifact=policy['artifact_fingerprint'],
        )

    return router
