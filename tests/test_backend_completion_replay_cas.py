"""Disposable-Neo4j proof: duplicate terminal IDs never mutate another attempt.

This test only uses conftest.neo4j_container and never touches the live graph.
"""
from __future__ import annotations

import pytest


CLAIM_SESSION = 'bounded-session-1'
CLAIM_AGENT = 'node-a'
RESULT = {'accepted': True, 'review': 'source checked'}
SUMMARY = 'Checked source and verified changes'


def prepare(neo, task_id):
    with neo._session() as session:
        session.run(
            "CREATE (t:Task {id:$id,status:'READY',target_agent_id:'node-a',"
            "required_capabilities:['llm'],task_type:'llm'})",
            {'id': task_id},
        ).consume()
    claimed = neo.claim_task(
        task_id, CLAIM_AGENT, capabilities=['llm'],
        session_id=CLAIM_SESSION, lease_seconds=120,
    )
    assert claimed['claimed']
    assert claimed['task']['execution_attempt'] == 1
    return claimed['claim_id']


def complete(neo, task_id, original_claim_id, **changes):
    arguments = dict(
        task_id=task_id, agent_id=CLAIM_AGENT, status='DONE',
        summary=SUMMARY, result=RESULT,
        claim_id=original_claim_id, session_id=CLAIM_SESSION,
        idempotency_key='complete-once:'+task_id,
    )
    arguments.update(changes)
    return neo.complete_task(**arguments)


def graph_state(neo, task_id):
    with neo._session() as session:
        record = session.run(
            "MATCH (t:Task {id:$id}) "
            "OPTIONAL MATCH (t)-[:EXECUTED_BY]->(r:AgentRun) "
            "RETURN t AS task,count(r) AS runs,"
            "collect(r.completion_id) AS completion_ids",
            {'id': task_id},
        ).single()
    return {'task': dict(record['task']), 'runs': record['runs'],
            'keys': record['completion_ids']}


def test_identical_replay_returns_original_run_and_changes_nothing(seeded_neo4j):
    neo=seeded_neo4j
    tid='cas-identical'
    cid=prepare(neo,tid)
    first=complete(neo,tid,cid)
    assert first['status']=='DONE' and first['run_id']
    state=graph_state(neo,tid)
    assert state['runs']==1
    assert state['task']['completed_by']==CLAIM_AGENT
    assert state['task']['claim_id']==cid
    assert state['task']['result_summary']==SUMMARY
    duplicate=complete(neo,tid,cid)
    assert duplicate is not None
    assert duplicate['idempotent'] is True
    assert duplicate['run_id']==first['run_id']
    assert graph_state(neo,tid)==state


@pytest.mark.parametrize('different',[
    {'status':'FAILED'},
    {'status':'CANCELLED'},
    {'agent_id':'node-other'},
    {'claim_id':'fake-claim'},
    {'claim_id':None},
    {'session_id':'other-session'},
    {'session_id':None},
    {'summary':'attempted replacement'},
    {'result':{'accepted':False}},
])
def test_changed_replay_returns_none_without_mutating_task_or_run(
    seeded_neo4j, different,
):
    neo=seeded_neo4j
    tid='cas-mismatch'
    cid=prepare(neo,tid)
    assert complete(neo,tid,cid) is not None
    before=graph_state(neo,tid)
    rejected=complete(neo,tid,cid,**different)
    assert rejected is None
    assert graph_state(neo,tid)==before


@pytest.mark.parametrize('change',[
    "SET t.status='CLAIMED', t.claimed_by='node-other', "
    "t.agent_session_id='new-session',t.claim_id='new-claim',t.execution_attempt=2",
    "SET t.status='READY',t.claimed_by=null,t.agent_session_id=null,"
    "t.claim_id=null,t.execution_attempt=1",
    "SET t.status='FAILED'",
    "SET t.agent_session_id='changed-session'",
    "SET t.claim_id='changed-claim'",
    "SET t.execution_attempt=2",
])
def test_stale_prior_key_cannot_change_reclaimed_or_modified_task(
    seeded_neo4j,change,
):
    neo=seeded_neo4j
    tid='cas-reclaimed'
    cid=prepare(neo,tid)
    assert complete(neo,tid,cid) is not None
    with neo._session() as session:
        session.run(f'MATCH (t:Task {{id:$id}}) {change}',{'id':tid}).consume()
    before=graph_state(neo,tid)
    assert complete(neo,tid,cid) is None
    assert complete(neo,tid,cid,status='FAILED') is None
    assert graph_state(neo,tid)==before


def test_duplicate_completion_key_is_ambiguous_and_denied(seeded_neo4j):
    neo=seeded_neo4j
    tid='cas-duplicate-key'
    cid=prepare(neo,tid)
    assert complete(neo,tid,cid)
    with neo._session() as session:
        session.run(
            "MATCH (t:Task {id:$id}) CREATE (r:AgentRun {id:'bad-duplicate',"
            "completion_id:$key,agent:'node-a',status:'DONE'}) "
            "CREATE (t)-[:EXECUTED_BY]->(r)",
            {'id':tid,'key':'complete-once:'+tid},
        ).consume()
    before=graph_state(neo,tid)
    assert complete(neo,tid,cid) is None
    assert graph_state(neo,tid)==before


def test_ready_task_cannot_be_completed_without_a_claim(seeded_neo4j):
    neo=seeded_neo4j
    tid='cas-ready-not-claimed'
    with neo._session() as session:
        session.run("CREATE (:Task {id:$id,status:'READY'})",{'id':tid}).consume()
    before=graph_state(neo,tid)
    assert complete(neo,tid,None) is None
    assert graph_state(neo,tid)==before


def test_expired_or_mismatched_active_claim_cannot_complete(seeded_neo4j):
    neo=seeded_neo4j
    tid='cas-expired'
    cid=prepare(neo,tid)
    with neo._session() as session:
        session.run(
            'MATCH (t:Task {id:$id}) SET t.lease_expires_at_ts=0',
            {'id':tid},
        ).consume()
    before=graph_state(neo,tid)
    assert complete(neo,tid,cid) is None
    assert graph_state(neo,tid)==before

    # A valid lease alone cannot make a session forged by another worker valid.
    with neo._session() as session:
        session.run(
            'MATCH (t:Task {id:$id}) SET t.lease_expires_at_ts=timestamp()+60000',
            {'id':tid},
        ).consume()
    before=graph_state(neo,tid)
    assert complete(neo,tid,cid,session_id='forged') is None
    assert graph_state(neo,tid)==before


def test_two_concurrent_completions_never_create_two_runs(seeded_neo4j):
    from concurrent.futures import ThreadPoolExecutor

    neo=seeded_neo4j
    tid='cas-two-writers'
    cid=prepare(neo,tid)
    def submit(_):
        return complete(neo,tid,cid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(submit,range(2)))
    before=graph_state(neo,tid)
    assert before['task']['status']=='DONE'
    assert before['runs']==1
    assert before['keys']==['complete-once:'+tid]
    assert sum(response is not None for response in outcomes)>=1
    assert complete(neo,tid,cid)['idempotent'] is True
    assert graph_state(neo,tid)==before
