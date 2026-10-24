#!/usr/bin/env python3
"""Opt-in generation-lock concurrency witness; disposable Neo4j ONLY."""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from neo4j import GraphDatabase

host=os.environ.get("HERMES_GENERATION_TEST_DB","")
if not host.startswith("hermes-gen-db-"):
    raise SystemExit("HOLD: exact disposable database required")
# Load only the staged research namespace, never the API image's live
# assistx package initializer (which opens application state stores).
import types
pkg=types.ModuleType("assistx")
pkg.__path__=["/candidate/src/assistx"]
sys.modules["assistx"]=pkg
from assistx import hermes_generation_fence as g

driver=GraphDatabase.driver("bolt://"+host+":7687",auth=None,
                            connection_timeout=8,max_connection_pool_size=8)
driver.verify_connectivity()
def session():
    return driver.session(database="neo4j")
def execute(query,**params):
    with session() as s:
        s.run(query,**params).consume()

execute("CREATE (:HermesCensusGeneration {owner:'hermes-local',generation:0,read_gate:0})")
execute("""
UNWIND range(0,620) AS i
CREATE (:Task {id:'synthetic_gen_'+toString(i),
    claimed_by:'hermes-local',target_agent_id:'hermes-local',
    claim_id:'claim_'+toString(i),agent_session_id:'session_'+toString(i),
    execution_attempt:1,lease_expires_at_ts:timestamp()+1000000,
    status:'CLAIMED'})
""")
base=g.generation_locked_candidate(session)
assert base["experiment_transaction_completed"] and base["candidate_count"]==621,base

writer_locked=threading.Event()
release_writer=threading.Event()
reader_done=threading.Event()
def writer_first():
    with session() as s:
        with s.begin_transaction() as tx:
            assert g.lock_writer_before_mutation(tx)==1
            tx.run("""MATCH(t:Task {id:'synthetic_gen_0'})
                SET t.claim_id='claim_reissued',t.execution_attempt=2""").consume()
            writer_locked.set()
            if not release_writer.wait(12):
                raise RuntimeError("writer_timeout")
            tx.commit()
def read_after_writer():
    r=g.generation_locked_candidate(session)
    reader_done.set()
    return r
with ThreadPoolExecutor(max_workers=2) as pool:
    w=pool.submit(writer_first)
    assert writer_locked.wait(8)
    r=pool.submit(read_after_writer)
    time.sleep(.35)
    assert not reader_done.is_set(),"reader bypassed writer lock"
    release_writer.set()
    w.result(timeout=15)
    after_writer=r.result(timeout=18)
assert after_writer["experiment_transaction_completed"]
assert after_writer["candidate_generation"]==1
assert after_writer["candidate_digest_sha256"]!=base["candidate_digest_sha256"]

reader_locked=threading.Event()
release_reader=threading.Event()
writer_done=threading.Event()
def reader_first():
    def callback():
        reader_locked.set()
        if not release_reader.wait(12):
            raise RuntimeError("reader_timeout")
    return g.generation_locked_candidate(session,on_locked_for_testing=callback)
def cooperating_writer():
    with session() as s:
        with s.begin_transaction() as tx:
            version=g.lock_writer_before_mutation(tx)
            tx.run("""MATCH(t:Task {id:'synthetic_gen_1'})
                SET t.claim_id='claim_after_reader',t.execution_attempt=2""").consume()
            tx.commit()
    writer_done.set()
    return version
with ThreadPoolExecutor(max_workers=2) as pool:
    r=pool.submit(reader_first)
    assert reader_locked.wait(8)
    w=pool.submit(cooperating_writer)
    time.sleep(.35)
    assert not writer_done.is_set(),"cooperating writer bypassed reader lock"
    release_reader.set()
    before_change=r.result(timeout=20)
    changed_version=w.result(timeout=20)
assert before_change["candidate_generation"]==1 and changed_version==2
latest=g.generation_locked_candidate(session)
assert latest["candidate_generation"]==2
assert latest["candidate_digest_sha256"]!=before_change["candidate_digest_sha256"]

# Counterexample: writer that skips generation lock commits DURING scan.
unfenced_committed=threading.Event()
def bypass():
    execute("""MATCH(t:Task {id:'synthetic_gen_2'})
        SET t.claim_id='claim_bypass_mutated',t.execution_attempt=2""")
    unfenced_committed.set()
def callback():
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(bypass).result(timeout=12)
    assert unfenced_committed.is_set()
bypassed=g.generation_locked_candidate(session,on_locked_for_testing=callback)
assert bypassed["experiment_transaction_completed"]
assert bypassed["candidate_generation"]==2
assert bypassed["source_generation_fenced"] is False
assert bypassed["production_dispatch_authorized"] is False

print(json.dumps({
    "schema":1,"mode":"hermes_disposable_generation_lock",
    "baseline_621_claims":True,"writer_first_blocks_reader":True,
    "reader_first_blocks_cooperating_writer":True,
    "unfenced_writer_bypass_counterexample":True,
    "real_claims_touched":0,"external_checkpoint_custody":False,
    "production_dispatch_authorized":False,"decision":"HOLD",
},sort_keys=True))
driver.close()
