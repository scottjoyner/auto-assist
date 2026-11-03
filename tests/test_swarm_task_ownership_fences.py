"""Disposable Neo4j: legacy swarm cannot overwrite another machine claim."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from assistx.swarm_core import fail_task, release_expired_task_leases, set_task_lease


OWNER='research-node'
SESSION='research-session-123'
FOREIGN='foreign-session-123'


def task(neo, task_id='swarm-fenced-task'):
    with neo._session() as s:
        s.run(
            "CREATE (:Task {id:$id,status:'READY',target_agent_id:$node,"
            "required_capabilities:['llm'],task_type:'llm'})",
            {'id':task_id,'node':OWNER},
        ).consume()
    claimed=neo.claim_task(task_id,agent_id=OWNER,capabilities=['llm'],
                           session_id=SESSION,lease_seconds=120)
    assert claimed['claimed'] is True
    return claimed['claim_id']


def snapshot(neo, task_id='swarm-fenced-task'):
    row=neo.get_task(task_id)
    assert row is not None
    return dict(row)


def fail(neo,cid,**overrides):
    settings={'task_id':'swarm-fenced-task','agent_id':OWNER,
              'error_summary':'scoped validation failed',
              'retryable':True,'session_id':SESSION,'claim_id':cid}
    settings.update(overrides)
    return fail_task(neo,**settings)


def lease(neo,cid,**overrides):
    settings={'task_id':'swarm-fenced-task','lease_seconds':120,
              'agent_id':OWNER,'session_id':SESSION,'claim_id':cid}
    settings.update(overrides)
    return set_task_lease(neo,**settings)


def test_valid_claim_scoped_failure_clears_old_owner_and_reclaims_as_ready(seeded_neo4j):
    neo=seeded_neo4j
    cid=task(neo)
    result=fail(neo,cid)
    assert result['status']=='READY'
    assert result.get('claimed_by') is None
    assert result.get('claim_id') is None
    assert result.get('agent_session_id') is None
    assert result.get('lease_expires_at_ts') is None
    assert result['failure_count']==1
    second=neo.claim_task('swarm-fenced-task',agent_id=OWNER,
        capabilities=['llm'],session_id='second-session-123')
    assert second['claimed'] is True and second['claim_id'] != cid
    before=snapshot(neo)
    assert fail(neo,cid) is None
    assert snapshot(neo)==before


def test_terminal_claim_scoped_failure_is_final(seeded_neo4j):
    neo=seeded_neo4j
    cid=task(neo)
    result=fail(neo,cid,retryable=False)
    assert result['status']=='FAILED'
    assert result['claim_id']==cid
    before=snapshot(neo)
    assert fail(neo,cid,retryable=True) is None
    assert snapshot(neo)==before


@pytest.mark.parametrize('overrides',[
    {'agent_id':'other-node'},
    {'session_id':FOREIGN},
    {'session_id':None},
    {'claim_id':'wrong-claim'},
    {'claim_id':None},
    {'claim_id':''},
])
def test_foreign_missing_or_stale_owner_fails_without_writes(seeded_neo4j,overrides):
    neo=seeded_neo4j
    cid=task(neo)
    before=snapshot(neo)
    assert fail(neo,cid,**overrides) is None
    assert snapshot(neo)==before


@pytest.mark.parametrize('changed',[
    "SET t.status='DONE'",
    "SET t.status='CANCELLED'",
    "SET t.status='REVIEW'",
    "SET t.claimed_by='other-node'",
    "SET t.agent_session_id='rotated-session'",
    "SET t.claim_id='rotated-claim'",
    "SET t.lease_expires_at_ts=0",
])
def test_revoked_expired_or_terminal_claim_cannot_be_failed_or_renewed(
    seeded_neo4j,changed,
):
    neo=seeded_neo4j
    cid=task(neo)
    with neo._session() as s:
        s.run("MATCH (t:Task {id:'swarm-fenced-task'}) "+changed).consume()
    before=snapshot(neo)
    assert fail(neo,cid) is None
    assert lease(neo,cid) is False
    assert snapshot(neo)==before


@pytest.mark.parametrize('overrides',[
    {'agent_id':'other-node'},
    {'session_id':FOREIGN},
    {'claim_id':'old-claim'},
    {'claim_id':''},
    {'lease_seconds':0},
    {'lease_seconds':901},
    {'lease_seconds':True},
    {'lease_seconds':1.5},
])
def test_lease_renewal_requires_exact_live_owner_and_bounded_duration(
    seeded_neo4j,overrides,
):
    neo=seeded_neo4j
    cid=task(neo)
    before=snapshot(neo)
    assert lease(neo,cid,**overrides) is False
    assert snapshot(neo)==before


def test_valid_lease_renewal_updates_only_current_claim(seeded_neo4j):
    neo=seeded_neo4j
    cid=task(neo)
    assert lease(neo,cid,lease_seconds=135) is True
    after=snapshot(neo)
    assert after['status']=='CLAIMED'
    assert after['claimed_by']==OWNER
    assert after['claim_id']==cid
    assert after['agent_session_id']==SESSION
    assert after['lease_seconds']==135
    assert after['lease_expires_at_ts'] is not None


def test_expired_sweep_clears_claim_and_blocks_late_fail_or_renew(seeded_neo4j):
    neo=seeded_neo4j
    cid=task(neo)
    with neo._session() as s:
        s.run("MATCH (t:Task {id:'swarm-fenced-task'}) "
              "SET t.lease_expires_at_ts=0").consume()
    assert release_expired_task_leases(neo,now_ms=1)==1
    after=snapshot(neo)
    assert after['status']=='READY'
    assert after.get('claimed_by') is None
    assert after.get('claim_id') is None
    assert after.get('agent_session_id') is None
    assert after.get('lease_expires_at_ts') is None
    assert fail(neo,cid) is None
    assert lease(neo,cid) is False
    assert snapshot(neo)==after


def test_missing_lease_is_not_invented_as_expired(seeded_neo4j):
    neo=seeded_neo4j
    cid=task(neo)
    with neo._session() as s:
        s.run("MATCH (t:Task {id:'swarm-fenced-task'}) "
              "REMOVE t.lease_expires_at_ts").consume()
    before=snapshot(neo)
    assert release_expired_task_leases(neo,now_ms=10**13)==0
    assert snapshot(neo)==before
    assert fail(neo,cid) is None
    assert lease(neo,cid) is False


def test_zero_timestamp_cannot_consume_lease_expiring_at_zero(seeded_neo4j):
    neo=seeded_neo4j
    task(neo)
    with neo._session() as s:
        s.run("MATCH (t:Task {id:'swarm-fenced-task'}) "
              "SET t.lease_expires_at_ts=0").consume()
    assert release_expired_task_leases(neo,now_ms=0)==0
    assert snapshot(neo)['status']=='CLAIMED'
    assert release_expired_task_leases(neo,now_ms=1)==1


def test_competing_terminal_failure_and_renewal_cannot_both_restore_claim(seeded_neo4j):
    neo=seeded_neo4j
    cid=task(neo)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_failure=pool.submit(fail,neo,cid,retryable=False)
        future_renewal=pool.submit(lease,neo,cid,lease_seconds=60)
        failed=future_failure.result()
        renewed=future_renewal.result()
    result=snapshot(neo)
    assert failed is not None and failed['status']=='FAILED'
    assert result['status']=='FAILED'
    assert result['failure_count']==1
    # A renewal may have won *before* the terminal write, but cannot
    # resurrect a lease on an already terminal Task.
    assert renewed in (True,False)
    assert lease(neo,cid) is False
    assert fail(neo,cid) is None


@pytest.mark.parametrize('status', ['CLAIMED','RUNNING'])
def test_legacy_writers_refuse_explicit_machine_authorized_claim(seeded_neo4j,status):
    neo=seeded_neo4j
    cid=task(neo)
    with neo._session() as s:
        s.run("MATCH (t:Task {id:'swarm-fenced-task'}) "
              "SET t.machine_authorized=true,t.status=$status",
              {'status':status}).consume()
        s.run("MATCH (t:Task {id:'swarm-fenced-task'}) "
              "SET t.lease_expires_at_ts=0").consume()
    before=snapshot(neo)
    assert fail(neo,cid) is None
    assert lease(neo,cid) is False
    assert release_expired_task_leases(neo,now_ms=1)==0
    assert snapshot(neo)==before


def test_legacy_ready_failure_does_not_reassign_machine_authorized_task(seeded_neo4j):
    neo=seeded_neo4j
    tid='machine-ready-legacy-reject'
    with neo._session() as s:
        s.run("CREATE (:Task {id:$id,status:'READY',machine_authorized:true})",
              {'id':tid}).consume()
    before=snapshot(neo,tid)
    assert fail(neo,None,task_id=tid,session_id=None,claim_id=None) is None
    assert snapshot(neo,tid)==before


def test_old_id_only_lease_call_is_a_safe_noop(seeded_neo4j):
    neo=seeded_neo4j
    task(neo)
    before=snapshot(neo)
    assert set_task_lease(neo,'swarm-fenced-task',lease_seconds=300) is False
    assert snapshot(neo)==before


def test_expired_sweep_and_late_agent_mutation_converge_to_unclaimed_ready(seeded_neo4j):
    neo=seeded_neo4j
    cid=task(neo)
    with neo._session() as s:
        s.run("MATCH (t:Task {id:'swarm-fenced-task'}) "
              "SET t.lease_expires_at_ts=0").consume()
    with ThreadPoolExecutor(max_workers=2) as pool:
        reclaimed=pool.submit(release_expired_task_leases,neo,1)
        later_fail=pool.submit(fail,neo,cid)
        sweep_count=reclaimed.result()
        stale_change=later_fail.result()
    assert sweep_count==1
    assert stale_change is None
    final=snapshot(neo)
    assert final['status']=='READY'
    assert final.get('claimed_by') is None
    assert final.get('claim_id') is None
    assert final.get('agent_session_id') is None
    assert final.get('lease_expires_at_ts') is None
    assert final.get('failure_count') is None


def test_swarm_failure_api_forwards_claim_and_session(seeded_neo4j,monkeypatch):
    from assistx import swarm_routes

    neo=seeded_neo4j
    cid=task(neo)

    class ControlledNeo:
        def _session(self):
            return neo._session()
        def close(self):
            pass

    monkeypatch.setattr(swarm_routes,'_neo',lambda:ControlledNeo())
    response=swarm_routes.api_fail_task(
        'swarm-fenced-task',
        swarm_routes.TaskFailIn(agent_id=OWNER,error_summary='bounded error',
            retryable=True,session_id=SESSION,claim_id=cid),
        user='synthetic-operator',
    )
    assert response['task']['status']=='READY'
    assert response['task'].get('claim_id') is None
    assert snapshot(neo)['status']=='READY'


def test_swarm_failure_api_rejects_wrong_claim_with_no_mutation(seeded_neo4j,monkeypatch):
    from fastapi import HTTPException
    from assistx import swarm_routes

    neo=seeded_neo4j
    task(neo)
    before=snapshot(neo)

    class ControlledNeo:
        def _session(self):
            return neo._session()
        def close(self):
            pass

    monkeypatch.setattr(swarm_routes,'_neo',lambda:ControlledNeo())
    with pytest.raises(HTTPException) as raised:
        swarm_routes.api_fail_task(
            'swarm-fenced-task',
            swarm_routes.TaskFailIn(agent_id=OWNER,error_summary='untrusted',
                retryable=False,session_id=SESSION,claim_id='different-claim'),
            user='synthetic-operator',
        )
    assert raised.value.status_code==404
    assert snapshot(neo)==before
