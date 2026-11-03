"""Isolated fake-graph tests: no real fleet node, Neo4j, or Task mutation."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx import scoped_machine_poll as scoped


class Row(dict):
    pass


class Result:
    def __init__(self, rows):
        self.rows = [Row(x) for x in rows]

    def single(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class FakeNeo:
    def __init__(self, *, supply=2, runtime=1, ready=True,
                 capabilities='["chat","local_only"]', revoke_before_task=False,
                 revoke_physical_before_task=None, principal=1,
                 revoke_principal_before_task=False,
                 machine_task_approved=True):
        self.supply = supply
        self.runtime = runtime
        self.ready = ready
        self.capabilities = capabilities
        self.revoke_before_task = revoke_before_task
        self.revoke_physical_before_task = revoke_physical_before_task
        self.principal = principal
        self.revoke_principal_before_task = revoke_principal_before_task
        self.machine_task_approved = machine_task_approved
        self.calls = []
        self.close_calls = 0

    def _session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def close(self):
        self.close_calls += 1

    def run(self, query, parameters):
        assert query.lstrip().startswith('MATCH')
        assert 'CREATE ' not in query and 'SET ' not in query and 'DELETE ' not in query
        self.calls.append((query, dict(parameters)))
        if "MATCH (t:Task {status:'READY', target_agent_id:$node_id})" in query:
            assert parameters['node_id'] == 'xwing'
            assert parameters['limit'] <= 20
            assert parameters['generation'] == 17
            assert parameters['artifact'] == 'a' * 64
            assert parameters['token_epoch'] == 3
            assert parameters['max_age_ms'] == 90_000
            assert "MATCH (auth:MachinePrincipalAdmission {node_id:$node_id,status:'approved'})" in query
            assert "auth.projection_generation=$generation" in query
            assert "auth.token_epoch=$token_epoch" in query
            assert "auth.artifact_fingerprint=$artifact" in query
            assert "auth.expires_at_ts>timestamp()" in query
            assert "size(principals)=1" in query
            assert "t.machine_authorized=true" in query
            assert "t.machine_authorization_generation=$generation" in query
            assert "t.machine_authorization_approval_id=auth.approval_id" in query
            assert "t.owner_id IS NOT NULL" in query
            assert "t.claim_id IS NULL" in query
            assert "t.claimed_by IS NULL" in query
            assert "coalesce(t.execution_attempt,0)=0" in query
            assert "m.machine_chat_eligible=true" in query
            assert "MATCH (p:FleetProjectionState {name:'canonical',status:'approved'})" in query
            assert "MATCH (s:WorkSupplyState" in query
            assert "MATCH (r:RuntimeInstance {node_id:$node_id})" in query
            assert "(r)-[:SERVES]->(m:LoadedModelInstance)" in query
            assert "MATCH (a:AccessPath {runtime_instance_id:r.runtime_instance_id})" in query
            assert "MATCH (c:CapacityObservation {runtime_instance_id:r.runtime_instance_id})" in query
            assert "r.admitted,false" in query and "r.expires_at_ts,0)>timestamp()" in query
            assert "m.admitted,false" in query and "m.artifact_fingerprint=$artifact" in query
            assert "a.approved,false" in query and "a.expires_at_ts,0)>timestamp()" in query
            assert "c.approved,false" in query and "c.expires_at_ts,0)>timestamp()" in query
            assert "c.parallel_slots,0)>0" in query
            assert "m.capabilities_json" in query
            assert "t.required_capabilities=['llm']" in query
            assert 't.requires_approval = false' in query
            assert 't.paperclip_dispatched = false' in query
            assert 'coalesce(t.requires_approval,false)' not in query
            assert 'coalesce(t.paperclip_dispatched,false)' not in query
            assert 't.payload_json' not in query and 'RETURN t ' not in query
            if self.revoke_before_task or self.revoke_principal_before_task:
                return Result([])
            if not self.machine_task_approved:
                return Result([])
            if self.revoke_physical_before_task in ('runtime','model','access','capacity'):
                # Fake the *final* read of a revoked witness. No Task metadata
                # may be returned if any matching physical witness disappears.
                return Result([])
            roles = ('["embedding"]' if self.revoke_physical_before_task == 'chat'
                     else self.capabilities)
            return Result([{'id': 'assigned-llm-1', 'priority': 'LOW',
                            'model_capabilities': [roles]}] if self.ready else [])
        if 'WorkSupplyState' in query:
            assert parameters['generation'] == 17
            return Result([{'slots': self.supply}] if self.supply else [])
        if 'RuntimeInstance' in query:
            assert parameters['node_id'] == 'xwing'
            assert parameters['artifact'] == 'a' * 64
            assert parameters['generation'] == 17
            assert parameters['token_epoch'] == 3
            assert "MATCH (auth:MachinePrincipalAdmission {node_id:$node_id,status:'approved'})" in query
            assert "auth.token_epoch=$token_epoch" in query
            assert "auth.expires_at_ts>$now" in query
            assert 'm.artifact_fingerprint = $artifact' in query
            assert 'LoadedModelInstance' in query and 'AccessPath' in query
            assert 'CapacityObservation' in query
            return Result([{'admitted_runtimes': self.runtime,
                            'admitted_machine_principals': self.principal,
                            'model_capabilities': [self.capabilities]}])
        raise AssertionError('unexpected query: ' + query)


def app_factory(monkeypatch, *, enabled=True, policy=True, token='secret-x',
                supply=2, runtime=1, approved=True, ready=True,
                capabilities='["chat","local_only"]', policy_generation=17,
                artifact='a' * 64, revoke_before_task=False,
                revoke_physical_before_task=None, token_epoch=3,
                principal=1, revoke_principal_before_task=False,
                machine_task_approved=True):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_OBSERVATION', '1' if enabled else '0')
    db = FakeNeo(supply=supply, runtime=runtime, ready=ready,
                 capabilities=capabilities,
                 revoke_before_task=revoke_before_task,
                 revoke_physical_before_task=revoke_physical_before_task,
                 principal=principal,
                 revoke_principal_before_task=revoke_principal_before_task,
                 machine_task_approved=machine_task_approved)
    calls = []

    def projection(_factory, now):
        calls.append(now)
        if not approved:
            raise scoped.RuntimeProjectionBlocked('expired approval')
        return {'generation': 17, 'revision': 'synthetic', 'expires_at_ts': now + 10000}

    monkeypatch.setattr(scoped, '_projection_state', projection)
    policies = {'xwing': {'enabled': True, 'audience': 'targeted-llm-observer',
                           'allowed_capabilities': ['llm'],
                           'projection_generation': policy_generation,
                           'artifact_fingerprint': artifact,
                           'token_epoch': token_epoch}} if policy else {}
    app = FastAPI()
    app.include_router(scoped.build_scoped_machine_router(
        lambda: db, lambda: {'xwing': token, 'other': 'secret-other'},
        lambda: policies,
    ))
    return TestClient(app), db, calls


def test_disabled_by_default_and_no_basic_operator_fallback(monkeypatch):
    client, db, calls = app_factory(monkeypatch, enabled=False)
    response = client.get('/api/fleet/nodes/xwing/assigned-task-hints',
                          headers={'X-Fleet-Node-Token': 'secret-x'},
                          auth=('admin', 'operator-password'))
    assert response.status_code == 404
    assert db.calls == [] and calls == []
    client, db, calls = app_factory(monkeypatch)
    response = client.get('/api/fleet/nodes/xwing/assigned-task-hints',
                          auth=('admin', 'operator-password'))
    assert response.status_code == 403
    assert db.calls == [] and calls == []


@pytest.mark.parametrize('node_id,secret', [
    ('xwing', 'wrong'), ('other', 'secret-x'), ('unknown', 'secret-x'),
    ('xwing', ''), ('xwing!', 'secret-x'),
])
def test_wrong_node_identity_never_reads_a_task(monkeypatch, node_id, secret):
    client, db, calls = app_factory(monkeypatch)
    response = client.get(f'/api/fleet/nodes/{node_id}/assigned-task-hints',
                          headers={'X-Fleet-Node-Token': secret})
    assert response.status_code == 403
    assert db.calls == [] and calls == []


def test_registered_node_without_explicit_policy_is_denied(monkeypatch):
    client, db, calls = app_factory(monkeypatch, policy=False)
    response = client.get('/api/fleet/nodes/xwing/assigned-task-hints',
                          headers={'X-Fleet-Node-Token': 'secret-x'})
    assert response.status_code == 403
    assert db.calls == [] and calls == []


@pytest.mark.parametrize('supply,runtime,approved', [
    (0, 1, True), (2, 0, True), (2, 1, False),
])
def test_expired_projection_zero_capacity_or_missing_runtime_holds_all_payloads(
    monkeypatch, supply, runtime, approved,
):
    client, db, calls = app_factory(monkeypatch, supply=supply,
                                    runtime=runtime, approved=approved)
    response = client.get('/api/fleet/nodes/xwing/assigned-task-hints',
                          headers={'X-Fleet-Node-Token': 'secret-x'})
    assert response.status_code == 503
    assert 'assigned-llm-1' not in response.text
    assert not any('MATCH (t:Task' in q for q, _ in db.calls)


def test_valid_scoped_observation_is_metadata_only_and_never_claim_authority(monkeypatch):
    client, db, calls = app_factory(monkeypatch)
    response = client.get('/api/fleet/nodes/xwing/assigned-task-hints',
                          headers={'X-Fleet-Node-Token': 'secret-x'})
    assert response.status_code == 200
    result = response.json()
    assert result['items'] == [{'id': 'assigned-llm-1', 'requires_capability': 'llm'}]
    assert result['projection_generation'] == 17
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert result['authorizes_claim'] is False
    assert result['authorizes_dispatch'] is False
    assert 'payload' not in response.text and 'secret' not in response.text
    assert len(db.calls) == 3 and len(calls) == 1


def test_token_does_not_grant_claim_or_legacy_operator_endpoint(monkeypatch):
    client, db, calls = app_factory(monkeypatch)
    headers={'X-Fleet-Node-Token': 'secret-x'}
    assert client.post('/api/tasks/t1/claim', json={'agent_id': 'xwing'},
                       headers=headers).status_code == 404
    assert client.get('/api/agent/tasks', headers=headers).status_code == 404
    assert db.calls == [] and calls == []


def test_bounds_and_absent_work_are_safe(monkeypatch):
    client, db, calls = app_factory(monkeypatch, ready=False)
    url='/api/fleet/nodes/xwing/assigned-task-hints'
    headers={'X-Fleet-Node-Token': 'secret-x'}
    assert client.get(url + '?limit=21', headers=headers).status_code == 422
    r=client.get(url + '?limit=20', headers=headers)
    assert r.status_code == 200 and r.json()['items'] == []
    assert r.json()['authorizes_claim'] is False


@pytest.mark.parametrize('role', [
    '["embedding","local_only"]', 'not-json', None, '["not-chat"]', '{}',
])
def test_embedding_or_malformed_runtime_evidence_never_returns_work_hints(
    monkeypatch, role,
):
    client, db, _ = app_factory(monkeypatch, capabilities=role)
    result = client.get('/api/fleet/nodes/xwing/assigned-task-hints',
                        headers={'X-Fleet-Node-Token': 'secret-x'})
    assert result.status_code == 503
    assert not any('MATCH (t:Task' in query for query, _ in db.calls)


@pytest.mark.parametrize('generation,artifact', [
    (16, 'a' * 64), (18, 'a' * 64), (17, 'not-a-fingerprint'),
    (17, 'b' * 63), (True, 'a' * 64), (None, 'a' * 64),
])
def test_stale_projection_or_unpinned_artifact_never_exposes_task_ids(
    monkeypatch, generation, artifact,
):
    client, db, _ = app_factory(
        monkeypatch, policy_generation=generation, artifact=artifact,
    )
    result = client.get('/api/fleet/nodes/xwing/assigned-task-hints',
                        headers={'X-Fleet-Node-Token': 'secret-x'})
    assert result.status_code in (403, 503)
    assert 'assigned-llm-1' not in result.text
    assert not any('MATCH (t:Task' in query for query, _ in db.calls)


def test_approval_revoked_before_final_read_yields_no_task_metadata(monkeypatch):
    client, db, _ = app_factory(monkeypatch, revoke_before_task=True)
    result=client.get('/api/fleet/nodes/xwing/assigned-task-hints',
                      headers={'X-Fleet-Node-Token':'secret-x'})
    assert result.status_code==200
    assert result.json()['items']==[]
    assert result.json()['authorizes_claim'] is False
    assert any('MATCH (p:FleetProjectionState' in query for query,_ in db.calls)


@pytest.mark.parametrize('revoked', ['runtime', 'model', 'access', 'capacity', 'chat'])
def test_physical_admission_revoked_between_reads_exposes_no_task_ids(
    monkeypatch, revoked,
):
    """A successful earlier witness read is not enough for Task metadata."""
    client, db, _ = app_factory(
        monkeypatch, revoke_physical_before_task=revoked,
    )
    response = client.get(
        '/api/fleet/nodes/xwing/assigned-task-hints',
        headers={'X-Fleet-Node-Token': 'secret-x'},
    )
    assert response.status_code == 200
    assert response.json()['items'] == []
    assert response.json()['authorizes_claim'] is False
    assert response.json()['authorizes_dispatch'] is False
    assert 'assigned-llm-1' not in response.text
    assert len(db.calls) == 3


@pytest.mark.parametrize('roles', ['not-json', 'null', '{}', '["embedding"]'])
def test_final_witness_rejects_non_chat_evidence(monkeypatch, roles):
    """Even final-read role evidence must be explicitly valid for chat."""
    client, db, _ = app_factory(monkeypatch)
    old_run = db.run

    def final_roles_replaced(query, parameters):
        result = old_run(query, parameters)
        if "MATCH (t:Task {status:'READY', target_agent_id:$node_id})" in query:
            return Result([{'id': 'assigned-llm-1', 'model_capabilities': [roles]}])
        return result

    db.run = final_roles_replaced
    response = client.get(
        '/api/fleet/nodes/xwing/assigned-task-hints',
        headers={'X-Fleet-Node-Token': 'secret-x'},
    )
    assert response.status_code == 200
    assert response.json()['items'] == []
    assert response.json()['authorizes_claim'] is False


@pytest.mark.parametrize('epoch', [None, 0, -1, True, '3', 3.0])
def test_machine_observer_requires_positive_pinned_token_epoch(monkeypatch, epoch):
    """A token alone must not supply an unspecified or malformed epoch."""
    client, db, calls = app_factory(monkeypatch, token_epoch=epoch)
    result = client.get(
        '/api/fleet/nodes/xwing/assigned-task-hints',
        headers={'X-Fleet-Node-Token': 'secret-x'},
    )
    assert result.status_code == 403
    assert 'assigned-llm-1' not in result.text
    assert db.calls == [] and calls == []


@pytest.mark.parametrize('principal_count', [0, 2])
def test_missing_or_ambiguous_graph_machine_principal_is_hard_hold(
    monkeypatch, principal_count,
):
    client, db, calls = app_factory(monkeypatch, principal=principal_count)
    result = client.get(
        '/api/fleet/nodes/xwing/assigned-task-hints',
        headers={'X-Fleet-Node-Token': 'secret-x'},
    )
    assert result.status_code == 503
    assert 'assigned-llm-1' not in result.text
    assert not any("MATCH (t:Task" in query for query, _ in db.calls)


def test_machine_principal_revoked_after_initial_witness_returns_no_hints(monkeypatch):
    client, db, _ = app_factory(monkeypatch, revoke_principal_before_task=True)
    result = client.get(
        '/api/fleet/nodes/xwing/assigned-task-hints',
        headers={'X-Fleet-Node-Token': 'secret-x'},
    )
    assert result.status_code == 200
    assert result.json()['items'] == []
    assert result.json()['authorizes_claim'] is False
    assert result.json()['authorizes_dispatch'] is False
    assert len(db.calls) == 3


def test_legacy_targeted_task_without_explicit_machine_approval_is_not_a_hint(monkeypatch):
    """Even an exact-target READY Task cannot be advertised without approval."""
    client, db, _ = app_factory(monkeypatch, machine_task_approved=False)
    result = client.get(
        '/api/fleet/nodes/xwing/assigned-task-hints',
        headers={'X-Fleet-Node-Token': 'secret-x'},
    )
    assert result.status_code == 200
    assert result.json()['items'] == []
    assert 'assigned-llm-1' not in result.text
    assert result.json()['authorizes_claim'] is False
