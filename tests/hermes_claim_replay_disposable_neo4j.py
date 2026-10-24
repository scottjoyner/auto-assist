#!/usr/bin/env python3
"""Disposable real Neo4j claim/replay denial; no production network or secrets."""
from __future__ import annotations

if __name__!="__main__":
    import unittest
    raise unittest.SkipTest("explicitly opted-in disposable Neo4j only")

import importlib.util
import os
from pathlib import Path
import sys
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from neo4j import GraphDatabase

assert os.getenv("HERMES_DISPOSABLE_NEO4J")=="synthetic-ci-only"
assert "NEO4J_URI" not in os.environ
root=Path(__file__).resolve().parents[1]
src=root/"src/assistx/neo4j_client.py"
assert src.is_file()
pkg=types.ModuleType("assistx")
pkg.__path__=[]
fake=types.ModuleType("assistx.auto_assign_client")
fake.notify_task_created=lambda *args,**kwargs:None
spec=importlib.util.spec_from_file_location("assistx.neo4j_client",src)
module=importlib.util.module_from_spec(spec)
from unittest.mock import patch
with patch.dict(sys.modules,{"assistx":pkg,"assistx.auto_assign_client":fake,
                              "assistx.neo4j_client":module}):
    spec.loader.exec_module(module)

driver=GraphDatabase.driver("bolt://127.0.0.1:7687",auth=None,connection_timeout=5)
for attempt in range(65):
    try:
        driver.verify_connectivity()
        break
    except Exception:
        if attempt==64:
            raise
        time.sleep(1)

prefix="SYNTHETIC_CLAIM_REPLAY_"+uuid.uuid4().hex[:12]

def session():
    return driver.session(database="neo4j")

def client():
    obj=object.__new__(module.Neo4jClient)
    obj._session=session
    return obj

def call(query,**kw):
    with session() as s:
        return s.run(query,kw).single()

def task(label,**props):
    tid=prefix+"_"+label
    assert call("CREATE (t:Task{id:$id}) SET t+=$props RETURN t.id AS id",
                id=tid,props={"status":"READY",**props})["id"]==tid
    return tid

def state(tid):
    return dict(call("MATCH(t:Task{id:$id}) RETURN t",id=tid)["t"])

def deny(value,label):
    assert value["claimed"] is False, (label,value)
    print("PASS "+label,flush=True)

try:
    wrong=task("wrong", status="CLAIMED",claimed_by="hermes-local",
               claim_id="replay-1",agent_session_id="session-1",
               lease_expires_at_ts=int(time.time()*1000)+60000,
               execution_attempt=1)
    deny(client().claim_task(wrong,"legacy-worker",
                            idempotency_key="replay-1"),"legacy_hermes_replay_denied")
    assert state(wrong)["claimed_by"]=="hermes-local"
    own=task("own", status="CLAIMED",claimed_by="legacy-worker",
             claim_id="legit-1",agent_session_id="session-own",
             execution_attempt=1)
    assert client().claim_task(
        own,"legacy-worker",session_id="session-own",
        idempotency_key="legit-1",
    )["claimed"]
    print("PASS legacy_exact_live_claim",flush=True)
    deny(client().claim_task(
        own,"legacy-other",idempotency_key="legit-1",
    ),"legacy_wrong_owner_denied")
    deny(client().claim_task(
        own,"legacy-worker",session_id="mismatched",
        idempotency_key="legit-1",
    ),"legacy_wrong_session_denied")

    terminal=task("terminal",status="DONE",claimed_by="legacy-worker",
                  claim_id="old-2",agent_session_id="old-session")
    deny(client().claim_task(
        terminal,"legacy-worker",idempotency_key="old-2",
    ),"legacy_terminal_replay_denied")
    assert state(terminal)["status"]=="DONE"

    historic=task("historic", status="READY", completed_by="hermes-local")
    deny(client().claim_task(
        historic,"legacy-worker",
    ),"historical_hermes_reclaim_denied")
    assert state(historic)["status"]=="READY"

    targeted=task("targeted",target_agent_id="hermes-local")
    got=client().claim_task(
        targeted,"hermes-local",session_id="fresh-session"
    )
    assert got["claimed"] and state(targeted)["claimed_by"]=="hermes-local"
    print("PASS fresh_targeted_hermes_claim",flush=True)

    prefixed=task("prefixed")
    deny(client().claim_task(
        prefixed,"hermes-disposable",
        session_id=None,
    ),"prefixed_hermes_missing_session_denied")
    assert client().claim_task(
        prefixed,"hermes-disposable",session_id="disposable-session",
    )["claimed"]
    print("PASS prefixed_hermes_session_qualified",flush=True)

    reissue=task("reissue",status="READY")
    with session() as holder:
        tx=holder.begin_transaction()
        try:
            assert tx.run(
                "MATCH(t:Task{id:$id}) SET t.claimed_by='hermes-local',"
                "t.completed_by='hermes-local' RETURN t.id AS id",
                id=reissue,
            ).single() is not None
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(client().claim_task,reissue,"legacy-worker")
                time.sleep(0.25)
                assert not future.done(),"unlocked READY claim observed stale owner"
                tx.commit()
                deny(future.result(timeout=20),"legacy_claim_hermes_reissue_race_denied")
        finally:
            if not tx.closed():
                tx.rollback()
    assert state(reissue)["status"]=="READY"
    # A batch update must not reset a CLAIMED / DONE Task to READY or
    # rewrite an existing Hermes-claimed task's targeting metadata.
    def idempotent_task(label,status,owner=None):
        tid=task(label,status=status,claimed_by=owner,
                 idempotency_key=prefix+"_ik_"+label)
        return tid
    def batch(label):
        return [{
            "idempotency_key":prefix+"_ik_"+label,
            "title":"synthetic retry","kind":"task",
            "target_agent_id":"legacy-batch",
        }]
    claimed=idempotent_task("batch_claimed","CLAIMED","hermes-local")
    assert client().create_tasks_batch(batch("batch_claimed"))==0
    assert state(claimed)["status"]=="CLAIMED"
    assert state(claimed)["claimed_by"]=="hermes-local"
    assert state(claimed).get("target_agent_id") is None
    print("PASS batch_cannot_reset_claimed_hermes",flush=True)

    terminal=idempotent_task("batch_done","DONE","legacy-worker")
    assert client().create_tasks_batch(batch("batch_done"))==0
    assert state(terminal)["status"]=="DONE"
    print("PASS batch_cannot_reopen_terminal",flush=True)

    historical=idempotent_task("batch_historical","FAILED","legacy-worker")
    call("MATCH(t:Task{id:$id}) SET t.completed_by='hermes-local'",
         id=historical)
    assert client().create_tasks_batch(batch("batch_historical"))==0
    assert state(historical)["status"]=="FAILED"
    print("PASS batch_historical_hermes_failed_not_reopened",flush=True)

    retry=idempotent_task("batch_retry","FAILED","legacy-worker")
    assert client().create_tasks_batch(batch("batch_retry"))==1
    assert state(retry)["status"]=="READY"
    print("PASS batch_nonhermes_failed_retry_allowed",flush=True)

    print("SYNTHETIC_CLAIM_REPLAY_DB_PASS=13",flush=True)
    print("PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)
finally:
    driver.close()
