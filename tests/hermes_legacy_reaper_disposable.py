"""Opt-in synthetic physical Neo4j reaper/retention race witness. HOLD."""
from __future__ import annotations
if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("physical Neo4j reaper probe is explicitly opt-in")
import ast
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from neo4j import GraphDatabase

host=os.getenv("HERMES_REAPER_SYNTHETIC_DB","")
assert host.startswith("hermes-reaper-db-"),"disposable DB host required"
source=Path("/candidate/src/assistx/maintenance.py")
tree=ast.parse(source.read_text())
needed={"_fenced_stale_claim_reap","_fenced_terminal_task_purge"}
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in needed]
guards=[n for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=="_LEGACY_SCOPE_GUARD" for t in n.targets)]
assert len(functions)==2 and len(guards)==1
ns={"STALE_CLAIM_REAP_SECONDS":120,"TASK_RETENTION_DAYS":1}
exec(compile(ast.fix_missing_locations(ast.Module(body=guards+functions,type_ignores=[])),str(source),"exec"),ns)
reap=ns["_fenced_stale_claim_reap"]
purge=ns["_fenced_terminal_task_purge"]
driver=GraphDatabase.driver("bolt://"+host+":7687",auth=None,connection_timeout=8,
                           max_connection_pool_size=8)
driver.verify_connectivity()

def run(query,**kwargs):
    with driver.session(database="neo4j") as s:
        return s.run(query,kwargs).single()

def create(tag,*,status="CLAIMED",owner="other-worker",marker=False,target="",
           expiry=-300000,updated=-100000000000):
    tid="SYNTHETIC_REAPER_"+tag+"_"+uuid.uuid4().hex[:8]
    run("""CREATE(t:Task {id:$id,status:$status,claimed_by:$owner,
        hermes_claim_scope:$marker,target_agent_id:$target,
        claim_id:'synthetic',lease_expires_at_ts:timestamp()+$expiry,
        updated_at_ts:timestamp()+$updated,created_at_ts:timestamp()+$updated})
        RETURN t.id AS id""",id=tid,status=status,owner=owner,marker=marker,
        target=target,expiry=expiry,updated=updated)
    return tid

def state(tid):
    return run("OPTIONAL MATCH(t:Task{id:$id}) RETURN t.status AS status,t.claimed_by AS owner,t.hermes_claim_scope AS marker",id=tid).data()

cases=[
    (create("owner",owner="hermes-local"),"CLAIMED"),
    (create("target",target="hermes-local"),"CLAIMED"),
    (create("marker",marker=True),"CLAIMED"),
    (create("normal"),"READY"),
]
with driver.session(database="neo4j") as s:reap(s)
for index,(tid,want) in enumerate(cases):
    got=state(tid)
    assert got["status"]==want,(index,got,want)
    print("PASS owner_scoped_reaper_case_"+str(index),flush=True)

again=create("normal_loop")
with driver.session(database="neo4j") as s:reap(s)
assert state(again)["status"]=="READY"
print("PASS standalone_reaper_method",flush=True)

kept=create("terminal_hermes",status="DONE",marker=True)
deleted=create("terminal_normal",status="DONE")
with driver.session(database="neo4j") as s:purge(s)
assert state(kept)["status"]=="DONE"
assert state(deleted)["status"] is None
print("PASS protected_terminal_history_retained",flush=True)

race=create("competing_claim")
with driver.session(database="neo4j") as lock:
    tx=lock.begin_transaction()
    tx.run("""MATCH(t:Task{id:$id})
        SET t.claimed_by='hermes-local',t.hermes_claim_scope=true,
            t.claim_id='new-hermes-claim' RETURN t.id AS id""",id=race).single()
    def competing_reap():
        with driver.session(database="neo4j") as s:return reap(s)
    with ThreadPoolExecutor(max_workers=1) as pool:
        f=pool.submit(competing_reap)
        time.sleep(0.2)
        tx.commit()
        f.result(timeout=20)
s=state(race)
assert s["status"]=="CLAIMED" and s["owner"]=="hermes-local",s
print("PASS concurrent_claim_reaper_after_lock_recheck",flush=True)
print("SYNTHETIC_CASES_PASS=7",flush=True)
print("PRODUCTION_AUTHORIZED=false",flush=True)
driver.close()
