"""Source-only machine claim contract tests. No live Neo4j or system mutation."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from assistx import scoped_machine_claim as scoped


FINGERPRINT = 'a' * 64


def request(**changes):
    d = dict(node_id='xwing', task_id='synthetic-task', session_id='session-test-1',
             projection_generation=17, artifact_fingerprint=FINGERPRINT,
             token_epoch=2, lease_seconds=120)
    d.update(changes)
    return scoped.ScopedClaimRequest(**d)


def policy(**changes):
    d = dict(enabled=True, node_id='xwing', audience='targeted-llm-claim',
             allowed_capabilities=['llm'], projection_generation=17,
             artifact_fingerprint=FINGERPRINT, token_epoch=2)
    d.update(changes)
    return d


class Result:
    def __init__(self, row):
        self.row = row

    def single(self):
        return self.row


class FakeTransaction:
    def __init__(self, fail_at=None):
        self.sql = []
        self.fail_at = fail_at

    def run(self, query, parameters):
        self.sql.append((query, dict(parameters)))
        if len(self.sql) == self.fail_at:
            return Result(None)
        if len(self.sql) == 1:
            return Result({'total_slots': 2, 'serial': 1})
        if len(self.sql) == 2:
            return Result({'task_id': 'synthetic-task',
                           'claim_id': parameters['claim_id'],
                           'session_id': 'session-test-1',
                           'generation': 17,
                           'lease_expires_at_ts': 123456,
                           'execution_attempt': 1})
        raise AssertionError('unexpected third query')


class FakeNeo:
    def __init__(self, fail_at=None):
        self.tx = FakeTransaction(fail_at=fail_at)
        self.rollbacks = 0
        self.commits = 0
        self.closed = 0

    def _session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute_write(self, func, params):
        try:
            out = func(self.tx, params)
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1
            return out

    def close(self):
        self.closed += 1


def test_feature_is_off_and_never_creates_database_client(monkeypatch):
    monkeypatch.delenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS', raising=False)
    create = Mock(side_effect=AssertionError('must not open any graph'))
    out = scoped.scoped_claim_task(request(), neo_factory=create,
                                   token_authenticated=True, approved_policy=policy())
    assert out == {'claimed': False, 'reason': 'feature_disabled'}
    create.assert_not_called()


@pytest.mark.parametrize('change', [
    {'node_id': '../admin'}, {'node_id': ''}, {'task_id': ''},
    {'session_id': 'x'}, {'session_id': 'a\n b'},
    {'artifact_fingerprint': 'no'}, {'token_epoch': 0},
    {'token_epoch': True}, {'projection_generation': True},
    {'lease_seconds': 0}, {'lease_seconds': 301}, {'lease_seconds': False},
])
def test_invalid_claim_request_does_not_enter_database(monkeypatch, change):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS', '1')
    create = Mock(side_effect=AssertionError('must not open graph'))
    assert scoped.scoped_claim_task(request(**change), neo_factory=create,
                                    token_authenticated=True,
                                    approved_policy=policy())['claimed'] is False
    create.assert_not_called()


@pytest.mark.parametrize('override,authenticated', [
    ({}, False), ({'enabled': False}, True),
    ({'node_id': 'another'}, True),
    ({'audience': 'targeted-llm-observer'}, True),
    ({'allowed_capabilities': ['llm', 'shell']}, True),
    ({'projection_generation': 16}, True),
    ({'artifact_fingerprint': 'b' * 64}, True),
    ({'token_epoch': 1}, True),
    ({'token_epoch': True}, True),
])
def test_non_scoped_or_stale_machine_authority_denied_pre_db(
    monkeypatch, override, authenticated,
):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS', '1')
    create = Mock(side_effect=AssertionError('must not open graph'))
    result = scoped.scoped_claim_task(
        request(), neo_factory=create, token_authenticated=authenticated,
        approved_policy=policy(**override))
    assert result == {'claimed': False, 'reason': 'node_authority_denied'}
    create.assert_not_called()


def test_success_synthetic_contract_is_exact_and_only_uses_transactional_cas(monkeypatch):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS', '1')
    neo = FakeNeo()
    out = scoped.scoped_claim_task(request(), neo_factory=lambda: neo,
                                   token_authenticated=True, approved_policy=policy())
    assert out['claimed'] is True
    assert out['receipt']['task_id'] == 'synthetic-task'
    assert out['receipt']['session_id'] == 'session-test-1'
    assert out['receipt']['generation'] == 17
    assert out['receipt']['execution_attempt'] == 1
    assert 'payload' not in str(out)
    assert neo.commits == 1 and neo.rollbacks == 0 and neo.closed == 1
    assert len(neo.tx.sql) == 2
    capacity, claim = [q for q, _ in neo.tx.sql]
    params = neo.tx.sql[1][1]
    assert 'SET w.scoped_claim_serial=' in capacity
    assert 'OPTIONAL MATCH (busy:Task)' in claim
    assert 'active_tasks < w.total_slots' in claim
    for required in (
        "status:'READY', target_agent_id:$node_id", "t.required_capabilities=['llm']",
        't.requires_approval=false', 't.paperclip_dispatched=false',
        't.machine_authorized=true', 't.machine_authorization_generation=$generation',
        't.machine_authorization_approval_id=auth.approval_id',
        'other.node_id<>$node_id',
        't.claim_id IS NULL', 't.agent_session_id IS NULL',
        't.owner_id IS NOT NULL', 't.lease_expires_at_ts IS NULL',
        'm.machine_chat_eligible=true', 'm.artifact_fingerprint=$artifact',
        'auth.token_epoch=$token_epoch', 'auth.expires_at_ts>timestamp()',
        'p.generation=$generation', "p.status", # overwritten below
        "WHERE busy.status IN ['CLAIMED','RUNNING','PAUSING']",
    ):
        if required == 'p.status':
            assert "status:'approved'" in claim
        else:
            assert required in claim
    assert params['lease_seconds'] == 120
    assert len(params['claim_id']) == 32


@pytest.mark.parametrize('fail_at,reason', [
    (1, 'capacity_or_approval_unavailable'),
    (2, 'ownership_or_claim_fence_denied'),
])
def test_denied_or_revoked_claim_rolls_back_capacity_write(monkeypatch, fail_at, reason):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS', '1')
    neo = FakeNeo(fail_at=fail_at)
    result = scoped.scoped_claim_task(request(), neo_factory=lambda: neo,
                                      token_authenticated=True, approved_policy=policy())
    assert result == {'claimed': False, 'reason': reason}
    assert neo.rollbacks == 1 and neo.commits == 0 and neo.closed == 1
