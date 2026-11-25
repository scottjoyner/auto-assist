"""Source-level research contract, not a native Neo4j lock witness."""
import ast
from pathlib import Path
import hashlib
import re
ROOT=Path(__file__).resolve().parents[1]
def query():
    m=ast.parse((ROOT/'src/assistx/neo4j_client.py').read_text())
    f=next(x for x in ast.walk(m) if isinstance(x,ast.FunctionDef) and x.name=='complete_task')
    return next(x.value for x in ast.walk(f) if isinstance(x,ast.Constant) and isinstance(x.value,str) and 'CREATE (r:AgentRun' in x.value)
def test_precheck_before_temporary_lock_and_post_lock_before_mutation():
    q=query(); start=q.index('WHERE ($completion_id'); guard=q.index('AND t._assistx_completion_lock IS NULL'); lock=q.index('SET t._assistx_completion_lock = true'); remove=q.index('REMOVE t._assistx_completion_lock'); after=q.index('WITH t',remove); check=q.index('WHERE ($completion_id',after); mutate=q.index('SET t.status=$status'); create=q.index('CREATE (r:AgentRun')
    assert start < guard < lock < remove < after < check < mutate < create
    assert q.count('SET t._assistx_completion_lock')==1
    assert q.count('REMOVE t._assistx_completion_lock')==1
    assert q.count('MATCH (t)-[:EXECUTED_BY]->(:AgentRun {completion_id:$completion_id})')==2
    for predicate in ('t.claimed_by=$agent_id','$claim_id IS NOT NULL','t.claim_id=$claim_id','($session_id IS NULL OR t.agent_session_id=$session_id)',"t.status IN ['CLAIMED','RUNNING']",'coalesce(t.execution_attempt, 0)=0'):
        assert predicate in q[start:lock] and predicate in q[check:mutate]
    assert 't.lease_expires_at_ts > timestamp()' in q[start:lock]
    assert 't.lease_expires_at_ts > datetime.realtime().epochMillis' in q[check:mutate]
def test_old_native_harness_pin_does_not_cover_candidate():
    assert hashlib.sha256(query().encode()).hexdigest()!='ef75210b5c841cebd272dd6973017c3d9695eec57831a3ec939d7f3c7975e9c3'
