"""Opt-in, disposable-only Neo4j claim revocation/reissue races.

Run only within an isolated Docker internal network, against a unique
synthetic DB container with no published ports and no production mounts.
"""
# Ordinary pytest collection must skip this explicitly invoked physical probe.
# It is not safe to import it without a disposable named database.
if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("opt-in Docker-internal Neo4j race witness")

import importlib.util
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from neo4j import GraphDatabase

host = os.environ.get("HERMES_SYNTHETIC_NEO4J_HOST", "")
if not host.startswith("hermes-race-db-"):
    raise SystemExit("HOLD: only exact disposable DB name permitted")
DB_URI = "bolt://" + host + ":7687"

# Import source from the mounted candidate, not from the running API.
SRC = Path("/candidate/src/assistx/neo4j_client.py")
if not SRC.is_file() or not str(SRC.resolve()).startswith("/candidate/src/assistx/"):
    raise SystemExit("HOLD: exact staged candidate source not present")
import types
pkg = types.ModuleType("assistx")
pkg.__path__ = []
aa = types.ModuleType("assistx.auto_assign_client")
aa.notify_task_created = lambda *a, **kw: None
spec = importlib.util.spec_from_file_location("assistx.neo4j_client", SRC)
module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"assistx":pkg, "assistx.auto_assign_client":aa,
                              "assistx.neo4j_client":module}):
    spec.loader.exec_module(module)
Neo = module.Neo4jClient

driver = GraphDatabase.driver(DB_URI, auth=None, connection_timeout=8,
                              max_connection_pool_size=8)
driver.verify_connectivity()

def once(query, **kwargs):
    with driver.session(database="neo4j") as sess:
        return sess.run(query, **kwargs).single()

def newtask(tag):
    tid = "SYNTHETIC_RACE_" + tag + "_" + uuid.uuid4().hex[:10]
    once("""CREATE (t:Task {id:$id, status:'CLAIMED',
        claimed_by:'hermes-local',claim_id:'claim-old', agent_session_id:'session-old',
        execution_attempt:1,lease_seconds:900,
        lease_expires_at_ts:timestamp()+120000}) RETURN t.id AS id""", id=tid)
    return tid

def state(tid):
    return once("""MATCH(t:Task {id:$id})
        OPTIONAL MATCH(t)-[:EXECUTED_BY]->(r:AgentRun)
        RETURN t.status AS status, t.claim_id AS claim,
               t.execution_attempt AS generation,
               count(r) AS runs""", id=tid).data()

def completion(tid):
    client=object.__new__(Neo)
    client._session=lambda: driver.session(database="neo4j")
    return client.complete_task(tid, "hermes-local", "DONE", summary="synthetic",
                                claim_id="claim-old",session_id="session-old")

def do_locked_interleave(tag, mutation, expected_claim, expected_status):
    tid=newtask(tag)
    acquired=threading.Event()
    finished=threading.Event()
    outcome={}
    with driver.session(database="neo4j") as session:
        tx=session.begin_transaction()
        changed=tx.run(mutation, id=tid).single()
        assert changed is not None, ("revocation/reissue precondition failed",tag)
        acquired.set()
        def attempt():
            acquired.wait(4)
            outcome["completion"]=completion(tid)
            finished.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            f=pool.submit(attempt)
            # Permit competing query to reach Neo4j while task write lock is held.
            time.sleep(0.15)
            if finished.is_set():
                raise AssertionError("competing completion bypassed locked Task")
            tx.commit()
            f.result(timeout=18)
    actual=state(tid)
    assert outcome["completion"] is None,(tag,"stale completion reported success")
    assert actual["status"]==expected_status,(tag,actual)
    assert actual["claim"]==expected_claim,(tag,actual)
    assert actual["runs"]==0,(tag,"post-revocation AgentRun exists",actual)
    print("PASS "+tag+" stale_complete_denied runs=0",flush=True)

cancel="""MATCH(t:Task {id:$id}) WHERE t.status='CLAIMED'
  SET t.status='CANCELLED',t.claim_id='claim-revoked',
      t.lease_expires_at_ts=timestamp()-10
  RETURN t.id AS id"""
reissue="""MATCH(t:Task {id:$id}) WHERE t.status='CLAIMED'
  SET t.claim_id='claim-new',t.agent_session_id='session-new',
      t.execution_attempt=2,t.lease_expires_at_ts=timestamp()+120000
  RETURN t.id AS id"""
for i in range(4):
    do_locked_interleave("cancel"+str(i),cancel,"claim-revoked","CANCELLED")
    do_locked_interleave("reissue"+str(i),reissue,"claim-new","CLAIMED")

# Reverse serial order: completed Task is not subsequently revocable.
for i in range(3):
    tid=newtask("reverse"+str(i))
    with driver.session(database="neo4j") as session:
        tx=session.begin_transaction()
        assert tx.run("""MATCH(t:Task {id:$id}) WHERE t.status='CLAIMED'
            SET t.status='DONE'
            CREATE (r:AgentRun {id:$runid,task_id:$id,status:'DONE'})
            MERGE (t)-[:EXECUTED_BY]->(r) RETURN t.id AS id""",
            id=tid,runid="synthetic-"+uuid.uuid4().hex).single()
        finished=threading.Event()
        outcome={}
        def revoke():
            client = object.__new__(Neo)
            client._session=lambda: driver.session(database="neo4j")
            outcome["revoked"]=client.update_task_status(tid, "CANCELLED")
            finished.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(revoke)
            time.sleep(0.15)
            if finished.is_set():
                raise AssertionError("revocation bypassed Task write lock")
            tx.commit()
            future.result(timeout=18)
    row=state(tid)
    assert outcome["revoked"] is False,("revoke after completion succeeded",row)
    assert row["status"]=="DONE" and row["runs"]==1,row
    print("PASS reverse"+str(i)+" completed_first_revocation_denied",flush=True)

# P0 #428: exact Task-owned Hermes aliases must not return through legacy
# permissive claim branches, including when a non-Hermes identity calls them.
def alias_task(tag, owner, target="hermes-disposable"):
    task_id = "SYNTHETIC_HERMES_ALIAS_" + tag + "_" + uuid.uuid4().hex[:10]
    once("""CREATE (t:Task {id:$id, status:'CLAIMED',
        claimed_by:$owner,target_agent_id:$target,claim_id:'alias-claim',
        agent_session_id:'alias-session',execution_attempt:1,
        lease_seconds:900,lease_expires_at_ts:timestamp()+120000})
        RETURN t.id AS id""", id=task_id, owner=owner, target=target)
    return task_id

def alias_client():
    c = object.__new__(Neo)
    c._session = lambda: driver.session(database="neo4j")
    return c

tid = alias_task("prefixed-missing", "hermes-disposable")
assert alias_client().heartbeat_task(tid, "hermes-disposable",
                                     session_id="alias-session", claim_id=None) is None
assert state(tid)["status"] == "CLAIMED" and state(tid)["runs"] == 0
print("PASS prefixed_claimless_heartbeat_denied", flush=True)

tid = alias_task("prefixed-valid", "hermes-disposable")
assert alias_client().heartbeat_task(tid, "hermes-disposable",
                                     session_id="alias-session",
                                     claim_id="alias-claim") is not None
assert alias_client().complete_task(tid, "hermes-disposable", "DONE",
                                    session_id="alias-session",
                                    claim_id="alias-claim") is not None
assert state(tid)["status"] == "DONE" and state(tid)["runs"] == 1
print("PASS prefixed_valid_claim_completed", flush=True)

tid = alias_task("target-caller", "rogue-legacy")
assert alias_client().heartbeat_task(tid, "rogue-legacy") is None
assert alias_client().complete_task(tid, "rogue-legacy", "DONE") is None
assert state(tid)["status"] == "CLAIMED" and state(tid)["runs"] == 0
print("PASS target_owned_hermes_legacy_bypass_denied", flush=True)

tid = alias_task("claim-caller", "hermes-disposable", target="other-agent")
assert alias_client().heartbeat_task(tid, "rogue-legacy") is None
assert state(tid)["status"] == "CLAIMED" and state(tid)["runs"] == 0
print("PASS claimed_owner_hermes_legacy_bypass_denied", flush=True)

tid = alias_task("prior-replay", "rogue-legacy")
once("""MATCH (t:Task {id:$id})
    SET t.status='FAILED'
    CREATE (r:AgentRun {id:$run_id,completion_id:'alias-old',status:'FAILED'})
    MERGE (t)-[:EXECUTED_BY]->(r)
    RETURN r.id AS id""", id=tid, run_id=uuid.uuid4().hex)
assert alias_client().complete_task(tid, "rogue-legacy", "DONE",
                                    idempotency_key="alias-old") is None
assert state(tid)["status"] == "FAILED" and state(tid)["runs"] == 1
print("PASS cross_agent_idempotent_replay_denied", flush=True)

print(json.dumps({"schema":1,"mode":"hermes_disposable_neo4j_races",
                  "isolation":"docker_internal_network_only",
                  "forward_revoke_or_reissue_races":8,
                  "reverse_completion_wins":3,
                  "synthetic_cases_passed":16,
                  "hermes_alias_and_cross_agent_denials":5,
                  "live_claims_touched":0,
                  "production_deployment_authorized":False,"decision":"HOLD"},sort_keys=True))
driver.close()
