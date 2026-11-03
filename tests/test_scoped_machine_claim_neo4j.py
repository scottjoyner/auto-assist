"""Disposable Neo4j integration; NEVER connect to production or reuse its creds."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from neo4j import GraphDatabase

from assistx import scoped_machine_claim as c

FINGERPRINT = 'a'*64


@pytest.fixture(scope='module')
def isolated_driver(neo4j_container):
    driver=GraphDatabase.driver(neo4j_container['uri'], auth=(
        neo4j_container['user'], neo4j_container['password']))
    yield driver
    driver.close()


class IsolatedNeo:
    def __init__(self, driver):
        self.driver=driver

    def _session(self):
        return self.driver.session()

    def close(self):
        pass


def request(task_id='claim-safe-1', **changes):
    x=dict(node_id='xwing', task_id=task_id,
           session_id='scoped-session-1', projection_generation=17,
           artifact_fingerprint=FINGERPRINT, token_epoch=2,
           lease_seconds=120)
    x.update(changes)
    return c.ScopedClaimRequest(**x)


def policy(**changes):
    x=dict(node_id='xwing', enabled=True, audience='targeted-llm-claim',
           allowed_capabilities=['llm'], projection_generation=17,
           artifact_fingerprint=FINGERPRINT, token_epoch=2)
    x.update(changes)
    return x


def seed(driver, task_ids=('claim-safe-1',), *, supply=2,
         principal_epoch=2, machine_authorized=True, chat=True):
    now=int(time.time()*1000)
    with driver.session() as s:
        s.run('MATCH (n) DETACH DELETE n').consume()
        s.run('''
            CREATE (p:FleetProjectionState {
              name:'canonical',status:'approved',generation:17,
              revision:'test-gen-17',manifest_checksum:'synthetic-sha',
              approved_by:'synthetic-owner',approval_id:'synthetic-approval',
              expires_at_ts:$expiry})
            CREATE (w:WorkSupplyState {
              name:'canonical',projection_generation:17,
              allow_background:true,total_slots:$supply,free_slots:$supply,
              generated_at_ts:$now})
            CREATE (auth:MachinePrincipalAdmission {
              node_id:'xwing',status:'approved',projection_generation:17,
              token_epoch:$principal_epoch,artifact_fingerprint:$artifact,
              approved_by:'synthetic-owner',approval_id:'synthetic-node-approval',
              expires_at_ts:$expiry})
            CREATE (r:RuntimeInstance {
              node_id:'xwing',runtime_instance_id:'synthetic-runtime',
              admitted:true,status:'online',expires_at_ts:$expiry})
            CREATE (m:LoadedModelInstance {
              model_instance_id:'test-model',admitted:true,
              machine_chat_eligible:$chat,
              artifact_fingerprint:$artifact,expires_at_ts:$expiry})
            CREATE (r)-[:SERVES]->(m)
            CREATE (a:AccessPath {
              runtime_instance_id:'synthetic-runtime',approved:true,
              approved_by:'synthetic-owner',approval_id:'synthetic-access',
              expires_at_ts:$expiry})
            CREATE (cap:CapacityObservation {
              runtime_instance_id:'synthetic-runtime',approved:true,
              parallel_slots:$supply,approved_by:'synthetic-owner',
              approval_id:'synthetic-capacity',expires_at_ts:$expiry})
        ''',{'now':now,'expiry':now+600000,'supply':supply,
             'artifact':FINGERPRINT,'principal_epoch':principal_epoch,
             'chat':chat}).consume()
        for task in task_ids:
            s.run('''
            CREATE (t:Task {
               id:$id,status:'READY',target_agent_id:'xwing',
               required_capabilities:['llm'],requires_approval:false,
               paperclip_dispatched:false,machine_authorized:$auth,
               machine_authorization_generation:17,
               machine_authorization_approval_id:'synthetic-node-approval',
               owner_id:'synthetic-owner'})
            ''',{'id':task,'auth':machine_authorized}).consume()


def test_real_neo4j_scoped_claim_and_duplicate_denial(isolated_driver, monkeypatch):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    seed(isolated_driver)
    def factory():
        return IsolatedNeo(isolated_driver)

    result=c.scoped_claim_task(request(),neo_factory=factory,
        token_authenticated=True,approved_policy=policy())
    assert result['claimed'] is True
    assert result['receipt']['generation']==17
    assert result['receipt']['execution_attempt']==1
    retry=c.scoped_claim_task(request(),neo_factory=factory,
        token_authenticated=True,approved_policy=policy())
    assert retry=={'claimed':False,'reason':'ownership_or_claim_fence_denied'}
    with isolated_driver.session() as s:
        item=s.run('''MATCH (t:Task {id:'claim-safe-1'})
            MATCH (w:WorkSupplyState {name:'canonical'})
            RETURN t.status AS status,t.claim_id AS id,
                   t.execution_attempt AS attempt,t.agent_session_id AS sid,
                   w.scoped_claim_serial AS serial''').single()
        assert item['status']=='CLAIMED'
        assert item['sid']=='scoped-session-1'
        assert item['attempt']==1
        assert item['serial']==1  # denied transaction rolled back counter
        assert result['receipt']['claim_id']==item['id']


@pytest.mark.parametrize('mode', ['bad_epoch','no_task_authority','embedding_only',
                                   'no_capacity','missing_target',
                                   'bad_existing_claim','wrong_task_generation',
                                   'wrong_task_caps','approval_flag_absent',
                                   'approval_expired','unapproved_access',
                                   'wrong_artifact',
                                   'foreign_allocation',
                                   'wrong_machine_approval'])
def test_real_neo4j_negative_paths_have_zero_persisted_writes(
    isolated_driver, monkeypatch, mode,
):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    seed(isolated_driver, principal_epoch=1 if mode=='bad_epoch' else 2,
         machine_authorized=mode!='no_task_authority',
         chat=mode!='embedding_only',
         supply=0 if mode=='no_capacity' else 2)
    changes={
        'missing_target': "MATCH (t:Task {id:'claim-safe-1'}) REMOVE t.target_agent_id",
        'bad_existing_claim': "MATCH (t:Task {id:'claim-safe-1'}) SET t.claim_id='old-claim'",
        'wrong_task_generation': "MATCH (t:Task {id:'claim-safe-1'}) SET t.machine_authorization_generation=16",
        'wrong_task_caps': "MATCH (t:Task {id:'claim-safe-1'}) SET t.required_capabilities=[]",
        'approval_flag_absent': "MATCH (t:Task {id:'claim-safe-1'}) REMOVE t.requires_approval",
        'approval_expired': "MATCH (p:FleetProjectionState {name:'canonical'}) SET p.expires_at_ts=0",
        'unapproved_access': "MATCH (a:AccessPath) SET a.approved=false",
        'wrong_artifact': "MATCH (m:LoadedModelInstance) SET m.artifact_fingerprint='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'",
        'foreign_allocation': "MATCH (t:Task {id:'claim-safe-1'}) CREATE (a:AllocationReservation {status:'ACTIVE',node_id:'other-node',expires_at_ts:timestamp()+100000}) CREATE (t)-[:HAS_ALLOCATION]->(a)",
        'wrong_machine_approval': "MATCH (t:Task {id:'claim-safe-1'}) SET t.machine_authorization_approval_id='unrelated-approval'",
    }
    if mode in changes:
        with isolated_driver.session() as s:
            s.run(changes[mode]).consume()
    result=c.scoped_claim_task(request(),neo_factory=lambda:IsolatedNeo(isolated_driver),
                               token_authenticated=True,approved_policy=policy())
    assert result['claimed'] is False
    with isolated_driver.session() as s:
        row=s.run('''MATCH (t:Task {id:'claim-safe-1'})
            MATCH (w:WorkSupplyState {name:'canonical'})
            RETURN t.status AS status,t.claim_id AS claim,
                   w.scoped_claim_serial AS serial''').single()
        assert row['status']=='READY'
        # A pre-existing stale claim marker is preserved, not cleared or reused.
        assert row['claim']==('old-claim' if mode=='bad_existing_claim' else None)
        assert row['serial'] is None


def test_concurrent_scoped_claims_share_single_real_capacity_lock(
    isolated_driver, monkeypatch,
):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    seed(isolated_driver, ('parallel-one','parallel-two'), supply=1)
    def claim(which):
        return c.scoped_claim_task(
            request(task_id=which,session_id='session-'+which),
            neo_factory=lambda:IsolatedNeo(isolated_driver),
            token_authenticated=True,approved_policy=policy())
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(claim,['parallel-one','parallel-two']))
    assert sum(x['claimed'] for x in results)==1
    with isolated_driver.session() as s:
        rows=list(s.run("MATCH (t:Task) WHERE t.status='CLAIMED' RETURN t.id AS id"))
        assert len(rows)==1
        serial=s.run("MATCH (w:WorkSupplyState {name:'canonical'}) RETURN w.scoped_claim_serial AS seq").single()
        assert serial['seq']==1


def test_parallel_positive_claims_use_both_explicitly_approved_slots(
    isolated_driver, monkeypatch,
):
    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS','1')
    seed(isolated_driver, ('slot-one','slot-two'), supply=2)
    def claim(which):
        return c.scoped_claim_task(request(task_id=which,session_id='session-'+which),
                                   neo_factory=lambda:IsolatedNeo(isolated_driver),
                                   token_authenticated=True,approved_policy=policy())
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes=list(executor.map(claim,['slot-one','slot-two']))
    assert all(x['claimed'] for x in outcomes)
    with isolated_driver.session() as s:
        rows=list(s.run("MATCH (t:Task) WHERE t.status='CLAIMED' RETURN t.id AS id"))
        assert len(rows)==2
        seq=s.run("MATCH (w:WorkSupplyState {name:'canonical'}) RETURN w.scoped_claim_serial AS serial").single()
        assert seq['serial']==2


def test_combined_observer_then_claim_shared_graph_contract(isolated_driver, monkeypatch):
    """One disposable graph: observer hints are only strict, transient metadata.

    The final observer read requires explicit native chat capability evidence,
    whereas the claim CAS also requires independently authenticated machine
    principal, exact Task authorization and fenced runtime/claim evidence.
    No code in this test is registered as a production API route.
    """
    from assistx import scoped_machine_poll as observer

    monkeypatch.setenv('ASSISTX_ENABLE_SCOPED_NODE_CLAIMS', '1')
    seed(isolated_driver, task_ids=('cross-stack-task',), supply=1)

    def factory():
        return IsolatedNeo(isolated_driver)

    def observe():
        return observer.candidate_tasks(
            'xwing', neo_factory=factory, limit=10,
            expected_generation=17, expected_artifact=FINGERPRINT,
        )

    # The synthetic claim fixture's Boolean chat flag is not enough for
    # observer metadata: the admitted model role must be explicitly recorded.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as blocked:
        observe()
    assert blocked.value.status_code == 503
    assert blocked.value.detail == 'node_chat_model_unavailable'

    with isolated_driver.session() as session:
        session.run(
            """MATCH (m:LoadedModelInstance {model_instance_id:'test-model'})
               SET m.capabilities_json=$roles""",
            {'roles': '["chat","local_only"]'},
        ).consume()

    eligible = observe()
    assert eligible['items'] == [
        {'id': 'cross-stack-task', 'requires_capability': 'llm'}
    ]
    assert eligible['authorizes_claim'] is False
    assert eligible['authorizes_dispatch'] is False

    claim_result = c.scoped_claim_task(
        request(task_id='cross-stack-task'),
        neo_factory=factory,
        token_authenticated=True,
        approved_policy=policy(),
    )
    assert claim_result['claimed'] is True

    claimed = observe()
    assert claimed['items'] == []
    assert claimed['authorizes_claim'] is False
    assert claimed['authorizes_dispatch'] is False
