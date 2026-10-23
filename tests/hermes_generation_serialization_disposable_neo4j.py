#!/usr/bin/env python3
"""Disposable Neo4j participant-generation and ABA canary (NO-GO for prod).

This is an opt-in test of an isolated prototype. All Task/ledger IDs are
synthetic. No production URI, Tailscale access, credentials or fleet data.
"""
from __future__ import annotations

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("explicit disposable localhost Neo4j canary only")

import importlib.util
import os
from pathlib import Path
import sys
import threading
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from neo4j import GraphDatabase

assert os.environ.get("HERMES_DISPOSABLE_NEO4J")=="synthetic-ci-only"
assert "NEO4J_URI" not in os.environ
assert "NEO4J_AUTH" not in os.environ
root=Path(__file__).resolve().parents[1]/"src/assistx"
pkg=types.ModuleType("assistx")
pkg.__path__=[str(root)]
sys.modules["assistx"]=pkg
for name in ("hermes_claim_census","hermes_source_generation_research"):
    path=root/(name+".py")
    spec=importlib.util.spec_from_file_location("assistx."+name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules["assistx."+name]=module
    spec.loader.exec_module(module)
from assistx import hermes_source_generation_research as g

driver=GraphDatabase.driver("bolt://127.0.0.1:7687",auth=None,connection_timeout=5,
                            max_transaction_retry_time=10)
for n in range(75):
    try:
        driver.verify_connectivity()
        break
    except Exception:
        if n==74:
            raise
        time.sleep(1)

prefix="SYNTHETIC_GENERATION_"+uuid.uuid4().hex[:12]
def session():
    return driver.session(database="neo4j")
def call(sql,**kwargs):
    with session() as s:
        return s.run(sql,kwargs).single()
def state(tid):
    return dict(call("MATCH(t:Task{id:$id}) RETURN t",id=tid)["t"])
def gen():
    r=call("MATCH(g:HermesSourceGeneration{owner:'hermes-local'}) "
           "RETURN g.generation AS n")
    return r["n"] if r else None

try:
    # No test may silently provision a production schema or singleton.
    with session() as s:
        assert g.pinned_candidate_snapshot(s)["candidate_count"] is None
        try:
            g.advance_candidate_generation(s,expected_generation=0,
                                           mutate=lambda tx:True)
        except g.GenerationProtocolError: pass
        else: raise AssertionError("missing constraint erroneously accepted")
    print("PASS missing_constraint_denies",flush=True)

    call("""CREATE CONSTRAINT hermes_source_generation_owner_unique
            IF NOT EXISTS FOR (g:HermesSourceGeneration)
            REQUIRE g.owner IS UNIQUE""")
    assert call(
        "CREATE (g:HermesSourceGeneration{owner:'hermes-local',generation:0,"
        "lock_epoch:0}) RETURN g.generation AS gen"
    )["gen"]==0

    # Seed >500 source claims, then validate the single generation + digest
    # read under one ledger lock and one Neo4j transaction.
    call("""
    UNWIND range(0,620) AS i
    CREATE(t:Task{
        id:$prefix+toString(i), claimed_by:'hermes-local',
        target_agent_id:'hermes-local', claim_id:'synthetic-'+toString(i),
        agent_session_id:'synthetic-session', execution_attempt:1,
        lease_expires_at_ts:timestamp()+120000,status:'CLAIMED'
    })
    RETURN count(t) AS n
    """,prefix=prefix)
    tid=prefix+"0"
    with session() as s:
        first=g.pinned_candidate_snapshot(s)
    assert first["snapshot_generation"]==0
    assert first["candidate_count"]==621
    assert first["candidate_source_lock_acquired"]
    assert not first["authoritative_complete_census"]
    assert first["decision"]=="HOLD"
    print("PASS single_transaction_621_claim_pinned_snapshot",flush=True)

    def set_state(target):
        def mutate(tx):
            row=tx.run(
                "MATCH(t:Task{id:$id}) SET t.status=$status "
                "RETURN t.id AS id",{"id":tid,"status":target},
            ).single()
            return bool(row and row["id"]==tid)
        return mutate

    with session() as s:
        advanced=g.advance_candidate_generation(
            s,expected_generation=0,mutate=set_state("RUNNING"))
    assert advanced["generation"]==1 and gen()==1
    assert state(tid)["status"]=="RUNNING"
    assert advanced["production_dispatch_authorized"] is False
    print("PASS generation_and_task_mutation_commit_together",flush=True)

    with session() as s:
        try:
            g.advance_candidate_generation(
                s,expected_generation=0,mutate=set_state("DONE"))
        except g.GenerationProtocolError:pass
        else:raise AssertionError("stale source generation was not rejected")
    assert state(tid)["status"]=="RUNNING" and gen()==1
    print("PASS stale_expected_generation_denied",flush=True)

    with session() as s:
        try:
            g.advance_candidate_generation(
                s,expected_generation=1,
                mutate=lambda tx: (set_state("DONE")(tx) and False))
        except g.GenerationProtocolError:pass
        else:raise AssertionError("missing Task mutation ACK committed")
    assert state(tid)["status"]=="RUNNING" and gen()==1
    print("PASS atomic_rollback_when_task_ack_false",flush=True)

    before=None
    with session() as s:
        before=g.pinned_candidate_snapshot(s)
    assert before["snapshot_generation"]==1

    # Concurrent participating mutation holds ledger lock during its Task
    # write; a pinned census must wait and return the committed generation.
    locked=threading.Event()
    release=threading.Event()
    def held_writer():
        with session() as s:
            def mutate(tx):
                ok=set_state("PAUSING")(tx)
                locked.set()
                assert release.wait(timeout=15)
                return ok
            return g.advance_candidate_generation(
                s,expected_generation=1,mutate=mutate)
    def independent_read():
        with session() as s:
            return g.pinned_candidate_snapshot(s)
    with ThreadPoolExecutor(max_workers=2) as pool:
        write_future=pool.submit(held_writer)
        try:
            assert locked.wait(10)
            read_future=pool.submit(independent_read)
            time.sleep(0.3)
            assert not read_future.done(),"census escaped held ledger write lock"
        finally:
            release.set()
        committed=write_future.result(timeout=15)
        after=read_future.result(timeout=15)
    assert committed["generation"]==2
    assert after["snapshot_generation"]==2
    assert after["candidate_count"]==621
    assert after["candidate_digest_sha256"]!=before["candidate_digest_sha256"]
    assert state(tid)["status"]=="PAUSING"
    print("PASS snapshot_serializes_behind_participating_writer",flush=True)

    # ABA: Task claims digest can return to the exact original state, but
    # generation must record both transitions and stale writers must reject.
    with session() as s:
        mid=g.advance_candidate_generation(
            s,expected_generation=2,mutate=set_state("RUNNING"))
        aba=g.pinned_candidate_snapshot(s)
    assert mid["generation"]==3
    assert aba["candidate_digest_sha256"]==before["candidate_digest_sha256"]
    assert aba["snapshot_generation"]==3
    with session() as s:
        try:
            g.advance_candidate_generation(
                s,expected_generation=1,mutate=set_state("DONE"))
        except g.GenerationProtocolError:pass
        else:raise AssertionError("ABA stale generation falsely accepted")
    assert state(tid)["status"]=="RUNNING" and gen()==3
    print("PASS ABA_digest_equal_but_stale_generation_denied",flush=True)

    # Deliberately demonstrate the remaining P0: an unregistered legacy
    # direct Task writer can change a row without bumping generation. The
    # prototype MUST still say HOLD and cannot claim all-writer coverage.
    call("MATCH(t:Task{id:$id}) SET t.status='DONE' RETURN t.id AS id",id=tid)
    with session() as s:
        unsafe=g.pinned_candidate_snapshot(s)
    assert unsafe["snapshot_generation"]==3
    assert unsafe["candidate_digest_sha256"]!=aba["candidate_digest_sha256"]
    assert unsafe["all_writers_generation_fenced"] is False
    assert unsafe["authoritative_complete_census"] is False
    assert unsafe["production_dispatch_authorized"] is False
    print("PASS legacy_direct_writer_bypass_detected_as_unqualified",flush=True)

    print("SYNTHETIC_SOURCE_GENERATION_DB_PASS=7",flush=True)
    print("PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)
finally:
    # Only our synthetic nodes; canary schema exists only in disposable DB.
    try:
        call("MATCH(t:Task) WHERE t.id STARTS WITH $prefix "
             "DETACH DELETE t",prefix=prefix)
        call("MATCH(g:HermesSourceGeneration{owner:'hermes-local'}) "
             "DETACH DELETE g")
    finally:
        driver.close()
