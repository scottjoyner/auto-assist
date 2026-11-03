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
                 capabilities='["chat","local_only"]', revoke_before_task=False):
        self.supply = supply
        self.runtime = runtime
        self.ready = ready
        self.capabilities = capabilities
        self.revoke_before_task = revoke_before_task
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
            assert '$cutoff' in query and '$now' in query
            assert "MATCH (p:FleetProjectionState {name:'canonical',status:'approved'})" in query
            assert 'MATCH (s:WorkSupplyState' in query
            assert "t.required_capabilities=['llm']" in query
            assert 't.payload_json' not in query and 'RETURN t ' not in query
            return Result([{'id': 'assigned-llm-1', 'priority': 'LOW'}]
                          if self.ready and not self.revoke_before_task else [])
        if 'WorkSupplyState' in query:
            assert parameters['generation'] == 17
            return Result([{'slots': self.supply}] if self.supply else [])
        if 'RuntimeInstance' in query:
            assert parameters['node_id'] == 'xwing'
            assert parameters['artifact'] == 'a' * 64
            assert 'm.artifact_fingerprint = $artifact' in query
            assert 'LoadedModelInstance' in query and 'AccessPath' in query
            assert 'CapacityObservation' in query
            return Result([{'admitted_runtimes': self.runtime,
                            'model_capabilities': [self.capabilities]}])
        raise AssertionError('unexpected query: ' + query)


def app_factory(monkeypatch, *, enabled=True, policy=True, token='secret-x',
                supply=2, runtime=1, approved=True, ready=True,
                capabilities='["chat","local_only"]', policy_generation=17,
                artifact='a' * 64, revoke_before_task=False):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_OBSERVATION', '1' if enabled else '0')
    db = FakeNeo(supply=supply, runtime=runtime, ready=ready,
                 capabilities=capabilities,
                 revoke_before_task=revoke_before_task)
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
                           'artifact_fingerprint': artifact}} if policy else {}
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
