#!/usr/bin/env python3
"""Disposable Neo4j Task ticket + allocation admission canary; HOLD-only."""
from __future__ import annotations

if __name__!="__main__":
    import unittest
    raise unittest.SkipTest("disposable Neo4j execution only")

import importlib.util
import os
from pathlib import Path
import sys
import time
import types
import uuid
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from neo4j import GraphDatabase

assert os.getenv("HERMES_DISPOSABLE_NEO4J")=="synthetic-ci-only"
assert "NEO4J_URI" not in os.environ
src=Path(__file__).resolve().parents[1]/"src/assistx/neo4j_client.py"
pkg=types.ModuleType("assistx")
pkg.__path__=[]
fake=types.ModuleType("assistx.auto_assign_client")
fake.notify_task_created=lambda *a,**k:None
spec=importlib.util.spec_from_file_location("assistx.neo4j_client",src)
mod=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{"assistx":pkg,"assistx.auto_assign_client":fake,
                              "assistx.neo4j_client":mod}):
    spec.loader.exec_module(mod)

driver=GraphDatabase.driver("bolt://127.0.0.1:7687",auth=None,connection_timeout=5)
for i in range(75):
    try:
        driver.verify_connectivity()
        break
    except Exception:
        if i==74: raise
        time.sleep(1)

prefix="SYNTHETIC_TICKET_ALLOC_"+uuid.uuid4().hex[:12]
def session():
    return driver.session(database="neo4j")
def client():
    obj=object.__new__(mod.Neo4jClient)
    obj._session=session
    return obj
def call(query,**kwargs):
    with session() as s:
        return s.run(query,kwargs).single()
def make(label,**kw):
    tid=prefix+"_"+label
    assert call("CREATE(t:Task{id:$id}) SET t+=$props RETURN t.id AS id",
                id=tid,props={"status":"READY",**kw})["id"]==tid
    return tid
def get(tid):
    return dict(call("MATCH(t:Task{id:$id}) RETURN t",id=tid)["t"])
def refused(fn,label):
    try:
        fn()
    except ValueError:
        print("PASS "+label,flush=True)
        return
    raise AssertionError("expected rejected ticket mutation: "+label)

try:
    # New ticket and idempotent READY update still work.
    ik=prefix+"_new_ticket"
    fresh=client().upsert_ticket(title="created",idempotency_key=ik)
    assert get(fresh)["status"]=="READY"
    same=client().upsert_ticket(title="updated",idempotency_key=ik)
    assert fresh==same and get(fresh)["title"]=="updated"
    print("PASS create_and_safe_ready_ticket_retry",flush=True)

    # No late idempotent rewrite may change claimed/terminal metadata.
    claimed=make("claimed",status="CLAIMED", claimed_by="hermes-local",
                 claim_id="claimed-token",execution_attempt=1,
                 idempotency_key=prefix+"_claimed")
    refused(lambda:client().upsert_ticket(title="overwrite",idempotency_key=prefix+"_claimed"),
            "claimed_hermes_ticket_immutable")
    assert get(claimed)["status"]=="CLAIMED"
    assert get(claimed).get("title") is None

    done=make("done",status="DONE",claimed_by="legacy-agent",
              idempotency_key=prefix+"_done")
    refused(lambda:client().upsert_ticket(title="resurrect",idempotency_key=prefix+"_done"),
            "terminal_ticket_immutable")
    assert get(done)["status"]=="DONE"
    assert get(done).get("title") is None

    residual=make("residual",status="READY",
                  completed_by="hermes-local",
                  idempotency_key=prefix+"_residual")
    refused(lambda:client().upsert_ticket(title="overwrite",idempotency_key=prefix+"_residual"),
            "historical_owner_ticket_immutable")
    assert get(residual).get("title") is None

    # Interleave a pending retry behind a real Task write; a post-lock
    # owner recheck must refuse the overwritten metadata.
    raced=make("raced",status="READY",idempotency_key=prefix+"_raced")
    with session() as s:
        tx=s.begin_transaction()
        try:
            assert tx.run(
                "MATCH(t:Task{id:$id}) SET t.claimed_by='hermes-local', "
                "t.status='CLAIMED',t.execution_attempt=1 "
                "RETURN t.id AS id",id=raced,
            ).single() is not None
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(
                    client().upsert_ticket,title="stale-race",
                    idempotency_key=prefix+"_raced"
                )
                time.sleep(0.25)
                assert not future.done(),"upsert escaped Task owner write lock"
                tx.commit()
                try:future.result(timeout=20)
                except ValueError:pass
                else:raise AssertionError("stale upsert survived owner reissue")
        finally:
            if not tx.closed():tx.rollback()
    assert get(raced).get("title") is None
    print("PASS ticket_retry_owner_race_denied",flush=True)

    node=prefix+"_node"
    assert call("CREATE(n:SwarmNode{node_id:$node,is_blocked:false,"
                "weight:2,max_concurrent:2}) RETURN n.node_id AS id",
                node=node)["id"]==node

    ready=make("allocation_ready")
    allocation=client().reserve_task_allocation(
        task_id=ready,node_id=node,model_id="synthetic-model",
        snapshot_revision=1,actor="synthetic-operator"
    )
    assert allocation["reserved"] and get(ready)["target_agent_id"]==node
    print("PASS normal_allocation_allowed",flush=True)

    released=client().release_task_allocation(
        allocation["reservation"]["id"],"synthetic-operator")
    assert released["released"] and get(ready).get("target_agent_id") is None
    print("PASS normal_allocation_release_allowed",flush=True)

    historical=make("allocation_historical",completed_by="hermes-local")
    assert not client().reserve_task_allocation(
        task_id=historical,node_id=node,model_id=None,
        snapshot_revision=1,actor="synthetic-operator")["reserved"]
    assert get(historical).get("target_agent_id") is None
    print("PASS historical_hermes_allocation_denied",flush=True)

    active=make("allocation_claimed",status="CLAIMED",claimed_by="hermes-local",
                claim_id="current-token",execution_attempt=1)
    assert not client().reserve_task_allocation(
        task_id=active,node_id=node,model_id=None,
        snapshot_revision=1,actor="synthetic-operator")["reserved"]
    print("PASS active_hermes_allocation_denied",flush=True)

    blocked=make("allocation_release_claimed")
    held=client().reserve_task_allocation(
        task_id=blocked,node_id=node,model_id=None,
        snapshot_revision=1,actor="synthetic-operator")
    assert held["reserved"]
    call("MATCH(t:Task{id:$id}) SET t.status='CLAIMED',"
         "t.claim_id='live-claim',t.claimed_by='legacy-worker',"
         "t.execution_attempt=1 RETURN t.id AS id",id=blocked)
    deny=client().release_task_allocation(
        held["reservation"]["id"],"synthetic-operator")
    assert not deny["released"]
    assert get(blocked)["allocation_reservation_id"]==held["reservation"]["id"]
    print("PASS claimed_allocation_release_denied",flush=True)

    hermesnode=prefix+"_hermes-node"
    assert call("CREATE(n:SwarmNode{node_id:$id,is_blocked:false,"
                "weight:1}) RETURN n.node_id AS id",id="hermes-"+hermesnode)["id"]
    restricted=make("allocation_disallowed_target")
    assert not client().reserve_task_allocation(
        task_id=restricted,node_id="hermes-"+hermesnode,
        model_id=None,snapshot_revision=1,actor="synthetic-operator")["reserved"]
    assert get(restricted).get("target_agent_id") is None
    print("PASS unqualified_hermes_allocator_denied",flush=True)

    print("SYNTHETIC_TICKET_ALLOCATION_DB_PASS=10",flush=True)
    print("PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)
finally:
    driver.close()
