#!/usr/bin/env python3
"""Explicit opt-in real Neo4j synthetic admission races; never production."""
if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("physical disposable Neo4j admission witness only")

import importlib.util
import os
from pathlib import Path
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from neo4j import GraphDatabase

host=os.environ.get("HERMES_DISPOSABLE_NEO4J_HOST","")
assert host.startswith("hermes-admit-only-"), "invalid synthetic host"
src=Path("/candidate/src/assistx/neo4j_client.py")
assert src.is_file() and str(src.resolve()).startswith("/candidate/src/assistx/")
pkg=types.ModuleType("assistx"); pkg.__path__=[]
fake=types.ModuleType("assistx.auto_assign_client")
fake.notify_task_created=lambda *a,**k:None
spec=importlib.util.spec_from_file_location("assistx.neo4j_client",src)
m=importlib.util.module_from_spec(spec)
import unittest.mock
with unittest.mock.patch.dict(sys.modules,{"assistx":pkg,"assistx.auto_assign_client":fake,
                                          "assistx.neo4j_client":m}):
    spec.loader.exec_module(m)

driver=GraphDatabase.driver(f"bolt://{host}:7687",auth=None,connection_timeout=8)
driver.verify_connectivity()
def makeclient():
    cl=object.__new__(m.Neo4jClient)
    cl._session=lambda:driver.session(database="neo4j")
    return cl
def call(sql,**params):
    with driver.session(database="neo4j") as s:
        return s.run(sql,**params).single()
def task(name,status="READY",claim=None,session=None,agent=None,attempt=0,delta=120000):
    tid="SYNTHETIC_ADMISSION_"+name
    call("""CREATE(t:Task {id:$id,status:$status,claim_id:$claim,
    claimed_by:$agent,agent_session_id:$session,execution_attempt:$attempt,
    lease_expires_at_ts:timestamp()+$delta}) RETURN t.id""",
    id=tid,status=status,claim=claim,agent=agent,session=session,
    attempt=attempt,delta=delta)
    return tid
def current(tid):
    return call("""MATCH(t:Task{id:$id}) RETURN t.status AS status,
        t.claim_id AS claim,t.claimed_by AS agent,
        t.execution_attempt AS attempt""",id=tid).data()

# A valid idempotent Hermes retry is allowed only on a current live claim.
live=task("live","CLAIMED","claim-1","sess-1","hermes-local",1)
assert makeclient().claim_task(live,"hermes-local",session_id="sess-1",
                               idempotency_key="claim-1")["claimed"]
print("PASS current_idempotent_claim",flush=True)
for label,state,claim,session,agent,attempt,delta in (
    ("completed","DONE","claim-2","sess-2","hermes-local",1,120000),
    ("wrong_owner","CLAIMED","claim-3","sess-3","other-agent",1,120000),
    ("expired","CLAIMED","claim-4","sess-4","hermes-local",1,-10000),
    ("zero_attempt","CLAIMED","claim-5","sess-5","hermes-local",0,120000),
    ("wrong_session","CLAIMED","claim-6","sess-real","hermes-local",1,120000),
):
    tid=task(label,state,claim,session,agent,attempt,delta)
    wanted_session="sess-fake" if label=="wrong_session" else session
    result=makeclient().claim_task(tid,"hermes-local",session_id=wanted_session,
                                   idempotency_key=claim)
    assert not result["claimed"],(label,result)
    print("PASS stale_idempotent_"+label,flush=True)

fresh=task("fresh")
r=makeclient().claim_task(fresh,"hermes-local",session_id="fresh-session")
assert r["claimed"] and r["task"]["status"]=="CLAIMED"
print("PASS fresh_hermes_claim",flush=True)

for j in range(5):
    tid=task("cancel_race_"+str(j))
    done=Event()
    with driver.session(database="neo4j") as s:
        tx=s.begin_transaction()
        assert tx.run("""MATCH(t:Task{id:$id}) SET t.status='CANCELLED'
            RETURN t.id AS id""",id=tid).single() is not None
        def competing():
            out=makeclient().claim_task(tid,"hermes-local",session_id="race-session")
            done.set()
            return out
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut=pool.submit(competing)
            time.sleep(0.15)
            assert not done.is_set(), "claim succeeded before release of Task lock"
            tx.commit()
            result=fut.result(timeout=18)
    assert not result["claimed"],(j,result)
    assert current(tid)["status"]=="CANCELLED"
    print("PASS cancel_before_claim_"+str(j),flush=True)

# Old idempotency token is rejected after a separate claim generation wins.
newgen=task("new_generation","CLAIMED","claim-new","sess-new","hermes-local",2)
result=makeclient().claim_task(newgen,"hermes-local",session_id="sess-old",
                                idempotency_key="claim-old")
assert not result["claimed"]
print("PASS old_generation_cannot_reenter",flush=True)

print("SYNTHETIC_NEO4J_ADMISSION_CASES_PASS=13",flush=True)
print("PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)
driver.close()
