"""Disposable Neo4j verifies both legacy maintenance reapers' shared predicate."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from assistx import maintenance


def seed(neo, *, task_id='maintenance-synthetic', status='CLAIMED',
         machine=False, lease='expired'):
    now=int(time.time()*1000)
    fields={
        'id':task_id,'status':status,'machine_authorized':machine,
        'claimed_by':'node-a','claim_id':'claim-synthetic-1',
        'agent_session_id':'session-synthetic-1','claimed_at_ts':now-500000,
        'last_heartbeat_ts':0,'last_heartbeat_at_ts':0,
    }
    key='passive_claim_expires_at_ts' if status=='CLAIMED_PASSIVE' else 'lease_expires_at_ts'
    if lease=='expired':fields[key]=0
    elif lease=='future':fields[key]=now+600000
    elif lease=='missing':pass
    else:raise ValueError('unsupported fixture lease')
    if status=='CLAIMED_PASSIVE':
        fields.update({'passive_claim_id':'passive-claim',
                       'passive_claim_agent_id':'passive-node',
                       'passive_claim_lease_id':'passive-lease'})
    with neo._session() as session:
        session.run('CREATE (t:Task) SET t=$props',{'props':fields}).consume()
    return task_id


def reap(neo):
    with neo._session() as session:
        return maintenance._reap_stale_claims(session)


def state(neo,task_id='maintenance-synthetic'):
    task=neo.get_task(task_id)
    assert task
    return dict(task)


@pytest.mark.parametrize('status',['CLAIMED','RUNNING','CLAIMED_PASSIVE'])
def test_only_expired_explicit_legacy_lease_is_reaped(seeded_neo4j,status):
    neo=seeded_neo4j
    tid=seed(neo,status=status,lease='expired')
    assert reap(neo)==1
    after=state(neo,tid)
    assert after['status']=='READY'
    for key in ('claimed_by','claim_id','agent_session_id','claimed_at_ts',
                'lease_expires_at_ts','last_heartbeat_ts','last_heartbeat_at_ts',
                'passive_claim_id','passive_claim_agent_id','passive_claim_lease_id',
                'passive_claim_expires_at_ts'):
        assert after.get(key) is None, key
    assert after['last_passive_claim_result']=='expired_stale_reaped'
    assert reap(neo)==0
    assert state(neo,tid)==after


@pytest.mark.parametrize('status',['CLAIMED','RUNNING','CLAIMED_PASSIVE'])
@pytest.mark.parametrize('lease',['missing','future'])
def test_no_guessed_or_unexpired_lease_is_reaped(seeded_neo4j,status,lease):
    neo=seeded_neo4j
    tid=seed(neo,status=status,lease=lease)
    before=state(neo,tid)
    assert reap(neo)==0
    assert state(neo,tid)==before


@pytest.mark.parametrize('status',['CLAIMED','RUNNING','CLAIMED_PASSIVE'])
def test_machine_authorized_lease_is_outside_legacy_reaper(seeded_neo4j,status):
    neo=seeded_neo4j
    tid=seed(neo,status=status,machine=True,lease='expired')
    before=state(neo,tid)
    assert reap(neo)==0
    assert state(neo,tid)==before


@pytest.mark.parametrize('status',['DONE','FAILED','CANCELLED','READY','REVIEW'])
def test_terminal_or_unclaimed_task_not_reaped(seeded_neo4j,status):
    neo=seeded_neo4j
    tid=seed(neo,status=status,lease='expired')
    before=state(neo,tid)
    assert reap(neo)==0
    assert state(neo,tid)==before


def test_synthetic_claim_cannot_be_resurrected_by_late_heartbeat(seeded_neo4j):
    neo=seeded_neo4j
    tid=seed(neo,lease='expired')
    assert reap(neo)==1
    before=state(neo,tid)
    result=neo.heartbeat_task(tid,agent_id='node-a',status='RUNNING',
        session_id='session-synthetic-1',claim_id='claim-synthetic-1')
    assert result is None
    assert state(neo,tid)==before


def test_parallel_reapers_consume_only_one_expired_task(seeded_neo4j):
    neo=seeded_neo4j
    tid=seed(neo,lease='expired')
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(lambda _:reap(neo),range(2)))
    assert sum(outcomes)==1
    after=state(neo,tid)
    assert after['status']=='READY'
    assert after.get('claim_id') is None
    assert reap(neo)==0


def test_source_has_one_shared_reaper_query_for_both_call_sites():
    from inspect import getsource
    source=getsource(maintenance)
    assert source.count('MATCH (t:Task)')>=1
    assert source.count('_reap_stale_claims(s)')==2
    assert source.count('WHERE coalesce(t.machine_authorized,false)=false')==1
    assert 't.lease_expires_at_ts IS NOT NULL' in maintenance._STALE_CLAIM_REAP_CYPHER
    assert 't.passive_claim_expires_at_ts IS NOT NULL' in maintenance._STALE_CLAIM_REAP_CYPHER


def test_maintenance_retention_never_deletes_machine_result_without_custody(
    seeded_neo4j,monkeypatch,
):
    from assistx import neo4j_client

    neo=seeded_neo4j
    with neo._session() as session:
        session.run("CREATE (:Task {id:'machine-signed-result',status:'DONE',"
                    "machine_authorized:true,updated_at_ts:0})").consume()
        session.run("CREATE (:Task {id:'old-legacy-result',status:'DONE',"
                    "machine_authorized:false,updated_at_ts:0})").consume()

    class NeoProxy:
        def _session(self):
            return neo._session()
        def close(self):
            pass

    monkeypatch.setattr(neo4j_client,'Neo4jClient',lambda:NeoProxy())
    monkeypatch.setattr(maintenance.answers_store,'rebuild_index',lambda:{'reindexed':0})
    monkeypatch.setattr(maintenance,'_reschedule',lambda:None)
    report=maintenance.maintenance_job()
    assert report['tasks_deleted']>=1
    assert neo.get_task('machine-signed-result')['status']=='DONE'
    assert neo.get_task('old-legacy-result') is None


def test_legacy_heartbeat_cannot_extend_approved_machine_claim(seeded_neo4j):
    neo=seeded_neo4j
    tid=seed(neo,lease='future',machine=True)
    before=state(neo,tid)
    out=neo.heartbeat_task(tid,agent_id='node-a',status='RUNNING',
        session_id='session-synthetic-1',claim_id='claim-synthetic-1')
    assert out is None
    assert state(neo,tid)==before
