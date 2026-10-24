#!/usr/bin/env python3
"""Disposable Neo4j-only atomic source receipt acceptance; production HOLD.

This test uses only an ephemeral localhost Enterprise database under a CI
marker; no live Neo4j URI, credential, services, Tailscale or NAS.
"""
from __future__ import annotations

if __name__!="__main__":
    import unittest
    raise unittest.SkipTest("only run against explicit disposable localhost Neo4j")

import importlib.util
import os
from pathlib import Path
import sys
import time
import types
import uuid

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError,ServiceUnavailable

assert os.environ.get("HERMES_ENTERPRISE_RBAC_CANARY")=="synthetic-ci-only"
assert os.environ.get("GITHUB_ACTIONS")=="true"
assert "NEO4J_URI" not in os.environ
assert "NEO4J_PASSWORD" not in os.environ
assert "NEO4J_AUTH" not in os.environ
password=os.environ.get("HERMES_RBAC_EPHEMERAL_ADMIN_PASSWORD")
assert password and len(password)>=20
URI="bolt://127.0.0.1:7687"

src=Path(__file__).resolve().parents[1]/"src/assistx"
pkg=types.ModuleType("assistx")
pkg.__path__=[str(src)]
sys.modules["assistx"]=pkg
for name in (
    "hermes_claim_census","hermes_source_generation_research",
    "hermes_source_heartbeat_research","hermes_source_completion_research",
    "hermes_source_receipt_research",
):
    spec=importlib.util.spec_from_file_location("assistx."+name,src/(name+".py"))
    module=importlib.util.module_from_spec(spec)
    sys.modules["assistx."+name]=module
    spec.loader.exec_module(module)
from assistx import hermes_source_receipt_research as receipt
from assistx.hermes_source_generation_research import GenerationProtocolError

prefix="SYNTHETIC_RECEIPT_"+uuid.uuid4().hex[:14]
task_id=prefix+"_task"
second_task=prefix+"_rollback"
first_key=str(uuid.uuid4())
second_key=str(uuid.uuid4())
bad_key=str(uuid.uuid4())

driver=GraphDatabase.driver(URI,auth=("neo4j",password),connection_timeout=6)
for i in range(75):
    try:driver.verify_connectivity();break
    except (Neo4jError,ServiceUnavailable,OSError):
        if i==74:raise
        time.sleep(1)

def run(q,**args):
    with driver.session(database="neo4j") as s:
        return s.run(q,args).single()

def generation():
    return run("MATCH(g:HermesSourceGeneration{owner:'hermes-local'}) "
               "RETURN g.generation AS n")["n"]

def state(tid):
    row=run("""MATCH(t:Task{id:$id})
        RETURN t.status AS status,t.heartbeat_at_ts AS heartbeat,
               t.completed_by AS completed_by""",id=tid)
    return dict(row)

def record(key):
    row=run("MATCH(r:HermesSourceReceipt{key:$key}) RETURN properties(r) AS props",key=key)
    return dict(row["props"]) if row else None

def command(op,key,tid=task_id,generation=0):
    d={"operation":op,"receipt_key":key,"expected_generation":generation,
       "task_id":tid,"claim_id":"synthetic-claim",
       "session_id":"synthetic-session","attempt":1}
    if op=="heartbeat":
        d["ttl_ms"]=30000
    return d

try:
    run("""
        CREATE CONSTRAINT hermes_source_generation_owner_unique IF NOT EXISTS
        FOR (g:HermesSourceGeneration) REQUIRE g.owner IS UNIQUE
    """)
    run("""
        CREATE CONSTRAINT hermes_source_receipt_key_unique IF NOT EXISTS
        FOR (r:HermesSourceReceipt) REQUIRE r.key IS UNIQUE
    """)
    run("CREATE (g:HermesSourceGeneration{owner:'hermes-local',generation:0,lock_epoch:0})")
    for tid in (task_id,second_task):
        run("""
            CREATE(t:Task {
              id:$id,status:'RUNNING',claimed_by:'hermes-local',
              target_agent_id:'hermes-local',
              claim_id:'synthetic-claim',
              agent_session_id:'synthetic-session',execution_attempt:1,
              lease_expires_at_ts:timestamp()+120000,
              hermes_fence_check_epoch:0})
        """,id=tid)
    assert generation()==0
    assert not record(first_key)
    with driver.session(database="neo4j") as s:
        acknowledged=receipt.commit_source_receipt(
            s,**command("heartbeat",first_key))
    assert acknowledged["candidate_generation"]==1
    assert acknowledged["source_receipt_committed"] is True
    assert acknowledged["socket_delivery_durable"] is False
    assert acknowledged["independent_custody_verified"] is False
    assert acknowledged["decision"]=="HOLD"
    assert generation()==1
    assert type(state(task_id)["heartbeat"]) is int
    properties=record(first_key)
    assert properties
    assert set(properties)=={
        "key","owner","operation","request_digest",
        "source_generation","created_at_ts",
    },properties.keys()
    assert properties["operation"]=="heartbeat"
    assert properties["source_generation"]==1
    assert len(properties["request_digest"])==64
    assert "synthetic-task" not in str(properties)
    print("PASS source_heartbeat_receipt_atomically_committed",flush=True)

    # Simulate a lost socket response. A NEW session, opened by an isolated
    # reader, may confirm the committed source mutation without replaying it.
    with driver.session(database="neo4j") as s:
        reconciled=receipt.reconcile_source_receipt(
            s,**command("heartbeat",first_key))
    assert reconciled["candidate_generation"]==1
    assert reconciled["source_receipt_reconciled"] is True
    assert reconciled["production_dispatch_authorized"] is False
    assert generation()==1
    print("PASS missing_socket_reply_reconciled_read_only_new_session",flush=True)

    for forged in (
        {**command("heartbeat",first_key),"claim_id":"wrong-claim"},
        {**command("heartbeat",first_key),"ttl_ms":40000},
        {**command("complete",first_key,generation=0)},
    ):
        try:
            with driver.session(database="neo4j") as s:
                receipt.reconcile_source_receipt(s,**forged)
        except GenerationProtocolError:
            pass
        else:
            raise AssertionError("mismatched source request matched receipt")
    assert generation()==1
    print("PASS receipt_request_digest_wrong_claim_ttl_operation_denied",flush=True)

    try:
        with driver.session(database="neo4j") as s:
            receipt.commit_source_receipt(
                s,**command("heartbeat",first_key))
    except GenerationProtocolError:
        pass
    else:
        raise AssertionError("replayed heartbeat created new receipt")
    assert generation()==1 and record(first_key)==properties
    print("PASS original_receipt_replay_no_second_lease_mutation",flush=True)

    # A duplicate key for a DIFFERENT Task at current generation must
    # roll back the Task write and ledger bump as one failed transaction.
    before=state(second_task)
    try:
        with driver.session(database="neo4j") as s:
            receipt.commit_source_receipt(
                s,**command("heartbeat",first_key,second_task,generation=1))
    except Neo4jError:
        pass
    else:
        raise AssertionError("duplicate receipt key did not fail transaction")
    assert generation()==1
    assert state(second_task)==before, (state(second_task),before)
    assert record(first_key)==properties
    print("PASS duplicate_receipt_unique_conflict_rolls_back_Task_and_generation",flush=True)

    wrong={**command("complete",bad_key,generation=1),"claim_id":"different"}
    try:
        with driver.session(database="neo4j") as s:
            receipt.commit_source_receipt(s,**wrong)
    except GenerationProtocolError:
        pass
    else:
        raise AssertionError("unmatched completion recorded a receipt")
    assert record(bad_key) is None and generation()==1
    assert state(task_id)["status"]=="RUNNING"
    print("PASS failed_Task_match_never_commits_receipt",flush=True)

    with driver.session(database="neo4j") as s:
        complete=receipt.commit_source_receipt(
            s,**command("complete",second_key,generation=1))
    assert complete["candidate_generation"]==2
    assert state(task_id)["status"]=="DONE"
    assert state(task_id)["completed_by"]=="hermes-local"
    assert generation()==2
    assert record(second_key)["source_generation"]==2
    with driver.session(database="neo4j") as s:
        r=receipt.reconcile_source_receipt(
            s,**command("complete",second_key,generation=1))
    assert r["source_receipt_reconciled"] is True
    assert r["resource_effects_verified"] is False
    assert generation()==2
    print("PASS completion_receipt_reconciled_after_distinct_source_commit",flush=True)

    # No signed, independently anchored high-water or external effect proof.
    assert acknowledged["production_dispatch_authorized"] is False
    assert complete["all_writers_generation_fenced"] is False
    print("HERMES_ATOMIC_SOURCE_RECEIPT_DB_PASS=6",flush=True)
    print("PRODUCTION_TASKS_TOUCHED=0;INDEPENDENT_CUSTODY=FALSE;DECISION=HOLD",flush=True)
finally:
    try:
        run("MATCH(t:Task) WHERE t.id STARTS WITH $prefix DETACH DELETE t",prefix=prefix)
        run("MATCH(r:HermesSourceReceipt) WHERE r.key IN $keys DETACH DELETE r",
            keys=[first_key,second_key,bad_key])
        run("MATCH(g:HermesSourceGeneration{owner:'hermes-local'}) DETACH DELETE g")
        run("DROP CONSTRAINT hermes_source_receipt_key_unique IF EXISTS")
        run("DROP CONSTRAINT hermes_source_generation_owner_unique IF EXISTS")
    finally:
        driver.close()
