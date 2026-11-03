"""Real concurrency/lease tests with an isolated disposable Neo4j test container."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import test_scoped_machine_claim_neo4j as seed_data
from assistx import scoped_machine_claim as claim
from assistx import scoped_machine_execution as execution

FINGERPRINT='a'*64
DIGEST='c'*64
ACCEPT_ID='d'*32


@pytest.fixture(scope='module')
def isolated_driver(neo4j_container):
    from neo4j import GraphDatabase
    driver=GraphDatabase.driver(neo4j_container['uri'],auth=(
        neo4j_container['user'],neo4j_container['password']))
    yield driver
    driver.close()


def create_claim(driver, *, task_id='claim-safe-1'):
    claim_info=claim.scoped_claim_task(
        seed_data.request(task_id=task_id),
        neo_factory=lambda:seed_data.IsolatedNeo(driver),
        token_authenticated=True,approved_policy=seed_data.policy())
    assert claim_info['claimed'] is True
    return claim_info['receipt']['claim_id']


def attempt(claim_id, **changes):
    values=dict(node_id='xwing',task_id='claim-safe-1',
        session_id='scoped-session-1',claim_id=claim_id,
        projection_generation=17,token_epoch=2,
        artifact_fingerprint=FINGERPRINT,lease_seconds=120)
    values.update(changes)
    return execution.ScopedExecutionRequest(**values)


def policy():
    return dict(enabled=True,node_id='xwing',
        audience='targeted-llm-execution',allowed_capabilities=['llm'],
        projection_generation=17,token_epoch=2,
        artifact_fingerprint=FINGERPRINT)


def operate(driver,request,*,finalize=False):
    return execution.scoped_execution_transition(
        request,finalize=finalize,
        token_authenticated=True,approved_policy=policy(),
        neo_factory=lambda:seed_data.IsolatedNeo(driver))


def acceptance(driver,valid_claim_id, **changes):
    now=int(time.time()*1000)
    record=dict(id=ACCEPT_ID,status='accepted',task_id='claim-safe-1',
        claim_id=valid_claim_id,session_id='scoped-session-1',node_id='xwing',
        projection_generation=17,token_epoch=2,
        artifact_fingerprint=FINGERPRINT,result_sha256=DIGEST,
        signature_verified=True,external_anchor_verified=True,
        external_checkpoint_hash='f'*64,accepted_at_ts=now,
        approved_by='synthetic-independent-custodian',
        approval_id='synthetic-independent-approval',
        expires_at_ts=now+300000,consumed=False)
    record.update(changes)
    with driver.session() as session:
        session.run('CREATE (a:TaskResultAcceptance) SET a=$record',
                    {'record':record}).consume()


def state(driver):
    with driver.session() as session:
        d=session.run('''MATCH (t:Task {id:'claim-safe-1'})
          MATCH (w:WorkSupplyState {name:'canonical'})
          RETURN t.status AS status,t.claim_id AS claim,
                 t.lease_expires_at_ts AS expires,
                 t.completion_acceptance_id AS acceptance,
                 w.scoped_execution_serial AS serial''').single()
        return dict(d)


def test_valid_claim_heartbeat_accepted_result_and_replay_denial(isolated_driver,monkeypatch):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    seed_data.seed(isolated_driver)
    cid=create_claim(isolated_driver)
    before=state(isolated_driver)
    heartbeat=operate(isolated_driver,attempt(cid))
    assert heartbeat['transition_applied'] is True
    after=state(isolated_driver)
    assert after['status']=='RUNNING'
    assert after['expires'] >= before['expires']
    assert after['serial']==1
    acceptance(isolated_driver,cid)
    result=operate(isolated_driver,attempt(cid,result_sha256=DIGEST,
                             acceptance_id=ACCEPT_ID),finalize=True)
    assert result['transition_applied'] is True
    assert result['graph_acceptance_predicates_checked'] is True
    done=state(isolated_driver)
    assert done['status']=='DONE' and done['acceptance']==ACCEPT_ID
    assert done['serial']==2
    replay=operate(isolated_driver,attempt(cid,result_sha256=DIGEST,
                             acceptance_id=ACCEPT_ID),finalize=True)
    assert replay['transition_applied'] is False
    assert state(isolated_driver)==done
    with isolated_driver.session() as session:
        proof=session.run('MATCH (a:TaskResultAcceptance {id:$id}) RETURN a.consumed AS consumed',
                          {'id':ACCEPT_ID}).single()
        assert proof['consumed'] is True


@pytest.mark.parametrize('case',[
    'wrong_claim','wrong_session','wrong_epoch','wrong_generation',
    'wrong_owner','lease_expired','task_pending','projection_revoked',
    'machine_revoked','access_revoked','model_revoked',
    'wrong_task_approval','foreign_allocation',
])
def test_heartbeat_denial_preserves_exact_attempt(isolated_driver,monkeypatch,case):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    seed_data.seed(isolated_driver)
    cid=create_claim(isolated_driver)
    changes={
        'wrong_claim': "MATCH (t:Task {id:'claim-safe-1'}) SET t.claim_id='different-claim'",
        'wrong_session': "MATCH (t:Task {id:'claim-safe-1'}) SET t.agent_session_id='another-session'",
        'wrong_epoch': "MATCH (t:Task {id:'claim-safe-1'}) SET t.machine_token_epoch=1",
        'wrong_generation': "MATCH (t:Task {id:'claim-safe-1'}) SET t.projection_generation=16",
        'wrong_owner': "MATCH (t:Task {id:'claim-safe-1'}) SET t.claimed_by='other-node'",
        'lease_expired': "MATCH (t:Task {id:'claim-safe-1'}) SET t.lease_expires_at_ts=0",
        'task_pending': "MATCH (t:Task {id:'claim-safe-1'}) SET t.status='PAUSING'",
        'projection_revoked': "MATCH (p:FleetProjectionState {name:'canonical'}) SET p.status='revoked'",
        'machine_revoked': "MATCH (a:MachinePrincipalAdmission) SET a.status='revoked'",
        'access_revoked': "MATCH (a:AccessPath) SET a.approved=false",
        'model_revoked': "MATCH (m:LoadedModelInstance) SET m.admitted=false",
        'wrong_task_approval': "MATCH (t:Task {id:'claim-safe-1'}) SET t.machine_authorization_approval_id='other-id'",
        'foreign_allocation': "MATCH (t:Task {id:'claim-safe-1'}) CREATE (a:AllocationReservation {status:'ACTIVE',node_id:'other',expires_at_ts:timestamp()+300000}) CREATE (t)-[:HAS_ALLOCATION]->(a)",
    }
    with isolated_driver.session() as session:
        session.run(changes[case]).consume()
    before=state(isolated_driver)
    out=operate(isolated_driver,attempt(cid))
    assert out['transition_applied'] is False
    assert state(isolated_driver)==before


@pytest.mark.parametrize('case',[
    'no_acceptance','wrong_digest','wrong_claim','wrong_node',
    'signature_unverified','anchor_unverified','receipt_used',
    'same_node_approver','acceptance_expired','mismatched_token_epoch',
    'empty_checkpoint','old_acceptance',
])
def test_finalization_denial_never_marks_task_done(isolated_driver,monkeypatch,case):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    seed_data.seed(isolated_driver)
    cid=create_claim(isolated_driver)
    if case!='no_acceptance':
        changes={
            'wrong_digest':{'result_sha256':'b'*64},
            'wrong_claim':{'claim_id':'a'*32},
            'wrong_node':{'node_id':'other'},
            'signature_unverified':{'signature_verified':False},
            'anchor_unverified':{'external_anchor_verified':False},
            'receipt_used':{'consumed':True},
            'same_node_approver':{'approved_by':'xwing'},
            'acceptance_expired':{'expires_at_ts':0},
            'mismatched_token_epoch':{'token_epoch':1},
            'empty_checkpoint':{'external_checkpoint_hash':''},
            'old_acceptance':{'accepted_at_ts':0},
        }
        acceptance(isolated_driver,cid,**changes.get(case,{}))
    before=state(isolated_driver)
    out=operate(isolated_driver,attempt(cid,result_sha256=DIGEST,
                        acceptance_id=ACCEPT_ID),finalize=True)
    assert out['transition_applied'] is False
    assert state(isolated_driver)==before


def test_two_simultaneous_finalizations_have_one_winner(isolated_driver,monkeypatch):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    seed_data.seed(isolated_driver)
    cid=create_claim(isolated_driver)
    acceptance(isolated_driver,cid)
    req=attempt(cid,result_sha256=DIGEST,acceptance_id=ACCEPT_ID)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:operate(isolated_driver,req,finalize=True),range(2)))
    assert sum(x['transition_applied'] for x in results)==1
    assert state(isolated_driver)['status']=='DONE'


def test_duplicate_independent_acceptance_id_does_not_mark_done(
    isolated_driver,monkeypatch,
):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_EXECUTION','1')
    seed_data.seed(isolated_driver)
    claim_id=create_claim(isolated_driver)
    acceptance(isolated_driver,claim_id)
    acceptance(isolated_driver,claim_id)
    before=state(isolated_driver)
    result=operate(isolated_driver,
        attempt(claim_id,result_sha256=DIGEST,acceptance_id=ACCEPT_ID),finalize=True)
    assert result['transition_applied'] is False
    assert state(isolated_driver)==before
    with isolated_driver.session() as session:
        rows=list(session.run('MATCH (a:TaskResultAcceptance {id:$id}) RETURN a.consumed AS consumed',{'id':ACCEPT_ID}))
        assert len(rows)==2 and all(row['consumed'] is False for row in rows)
