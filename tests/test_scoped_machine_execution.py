"""Synthetic deny-by-default tests. No deployed Task/Neo4j writes."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from assistx import scoped_machine_execution as e


CLAIM='b'*32
FINGERPRINT='a'*64
DIGEST='c'*64
ACCEPT='d'*32


def attempt(**changes):
    d=dict(node_id='xwing',task_id='controlled-task',session_id='session-safe-1',
           claim_id=CLAIM,projection_generation=17,token_epoch=2,
           artifact_fingerprint=FINGERPRINT,lease_seconds=120)
    d.update(changes)
    return e.ScopedExecutionRequest(**d)


def policy(**changes):
    d=dict(enabled=True,node_id='xwing',audience='targeted-llm-execution',
           allowed_capabilities=['llm'],projection_generation=17,token_epoch=2,
           artifact_fingerprint=FINGERPRINT)
    d.update(changes)
    return d


class FakeTx:
    def __init__(self, failure=None):
        self.queries=[]
        self.failure=failure

    def run(self, query, params):
        self.queries.append((query,dict(params)))
        index=len(self.queries)
        class Result:
            def single(self_inner):
                if index==self.failure:
                    return None
                return {'serial':1} if index==1 else {
                    'task_id':'controlled-task','claim_id':CLAIM,
                    'session_id':'session-safe-1','status':'DONE',
                    'acceptance_id':ACCEPT,'result_sha256':DIGEST}
        return Result()


class FakeNeo:
    def __init__(self,failure=None):
        self.tx=FakeTx(failure)
        self.closed=0
        self.committed=0
        self.rolled_back=0

    def _session(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def close(self):
        self.closed+=1

    def execute_write(self,cb):
        try:
            result=cb(self.tx)
        except Exception:
            self.rolled_back+=1
            raise
        self.committed+=1
        return result


def transition(monkeypatch,req=None,finalize=False,pol=None,authed=True,neo=None):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    db=neo or FakeNeo()
    return e.scoped_execution_transition(req or attempt(),finalize=finalize,
        token_authenticated=authed,approved_policy=pol or policy(),
        neo_factory=lambda:db),db


def test_flag_off_skips_all_database_access(monkeypatch):
    monkeypatch.delenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION',raising=False)
    create=Mock(side_effect=AssertionError('graph access forbidden'))
    assert e.scoped_execution_transition(attempt(),finalize=False,
        token_authenticated=True,approved_policy=policy(),neo_factory=create)=={
        'transition_applied':False,'reason':'feature_disabled'}
    create.assert_not_called()


@pytest.mark.parametrize('changes',[{'node_id':'../bad'},{'task_id':''},{'session_id':''},
    {'claim_id':'wrong'},{'claim_id':'A'*32},{'token_epoch':True},
    {'projection_generation':0},{'artifact_fingerprint':'wrong'},
    {'lease_seconds':0},{'lease_seconds':301},{'lease_seconds':True}])
def test_invalid_attempt_cannot_open_database(monkeypatch,changes):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    creator=Mock(side_effect=AssertionError('no db'))
    out=e.scoped_execution_transition(attempt(**changes),finalize=False,
        token_authenticated=True,approved_policy=policy(),neo_factory=creator)
    assert out['reason']=='invalid_attempt'
    creator.assert_not_called()


@pytest.mark.parametrize('changes,auth',[({},False),({'node_id':'other'},True),
    ({'token_epoch':1},True),({'token_epoch':True},True),
    ({'projection_generation':18},True),({'artifact_fingerprint':'b'*64},True),
    ({'allowed_capabilities':['llm','shell']},True),
    ({'audience':'targeted-llm-claim'},True),({'enabled':False},True)])
def test_wrong_machine_or_stale_policy_rejected_without_graph(monkeypatch,changes,auth):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    create=Mock(side_effect=AssertionError('no graph'))
    assert e.scoped_execution_transition(attempt(),finalize=False,
        token_authenticated=auth,approved_policy=policy(**changes),
        neo_factory=create)['reason']=='node_authority_denied'
    create.assert_not_called()


@pytest.mark.parametrize('digest,receipt',[('',ACCEPT),(DIGEST,''),('x',ACCEPT),
                                            (DIGEST,'xyz'),(None,ACCEPT),
                                            (DIGEST,None)])
def test_finalization_requires_exact_result_and_acceptance_ids(monkeypatch,digest,receipt):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    creator=Mock(side_effect=AssertionError('no db'))
    r=attempt(result_sha256=digest,acceptance_id=receipt)
    out=e.scoped_execution_transition(r,finalize=True,
        token_authenticated=True,approved_policy=policy(),neo_factory=creator)
    assert out['reason']=='invalid_attempt'
    creator.assert_not_called()


def test_heartbeat_cannot_carry_result_or_external_receipt(monkeypatch):
    result,db=transition(monkeypatch,attempt(result_sha256=DIGEST),finalize=False)
    assert result['reason']=='invalid_attempt' and db.tx.queries==[]


@pytest.mark.parametrize('failure,reason',[(1,'capacity_or_approval_unavailable'),
                                            (2,'attempt_or_acceptance_fence_denied')])
def test_unacknowledged_transaction_rolls_back_lock(monkeypatch,failure,reason):
    db=FakeNeo(failure=failure)
    out,_=transition(monkeypatch,neo=db)
    assert out=={'transition_applied':False,'reason':reason}
    assert db.committed==0 and db.rolled_back==1 and db.closed==1


def test_heartbeat_query_checks_exact_claim_and_lease(monkeypatch):
    out,db=transition(monkeypatch)
    assert out['transition_applied'] is True
    assert out['graph_acceptance_predicates_checked'] is False
    assert db.committed==1 and db.closed==1
    assert len(db.tx.queries)==2
    lock,heart=[q for q,_ in db.tx.queries]
    assert 'SET w.scoped_execution_serial=' in lock
    for x in ('t.claim_id=$claim_id','t.agent_session_id=$session_id',
              't.claimed_by=$node_id','t.execution_attempt=1',
              't.lease_expires_at_ts>timestamp()',
              'auth.token_epoch=$token_epoch',
              't.machine_authorization_approval_id=auth.approval_id',
              'm.artifact_fingerprint=$artifact',
              "t.status IN ['CLAIMED','RUNNING']"):
        assert x in heart
    assert "SET t.status='RUNNING'" in heart
    assert db.tx.queries[1][1]['claim_id']==CLAIM


def test_done_requires_independent_acceptance_and_is_atomically_consumed(monkeypatch):
    out,db=transition(monkeypatch,attempt(result_sha256=DIGEST,acceptance_id=ACCEPT),
                      finalize=True)
    assert out['transition_applied'] and out['graph_acceptance_predicates_checked']
    assert db.committed==1 and db.closed==1
    query,params=db.tx.queries[1]
    assert 'TaskResultAcceptance' in query
    assert 'acceptance.result_sha256=$result_sha256' in query
    assert 'acceptance.signature_verified=true' in query
    assert 'acceptance.external_anchor_verified=true' in query
    assert 'size(acceptance.external_checkpoint_hash)=64' in query
    assert 'acceptance.accepted_at_ts>=t.claimed_at_ts' in query
    assert 'acceptance.consumed=false' in query
    assert 'size(qualified_acceptances)=1' in query
    assert 'acceptance.approved_by<>$node_id' in query
    assert "SET t.status='DONE'" in query
    assert 'acceptance.consumed=true' in query
    assert params['result_sha256']==DIGEST and params['acceptance_id']==ACCEPT
    assert 'prompt' not in str(out) and 'content' not in str(out)
