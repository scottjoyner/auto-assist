#!/usr/bin/env python3
"""Isolated Enterprise role + enrolled Hermes Task writer end-to-end witness.

Never production: exact localhost, opt-in synthetic marker, disposable users,
credentials created in runner and memory only. Explicitly demonstrates
that broker's DB writer credential alone cannot force generation protocol;
production must isolate its key and narrow the broker's executable surface.
"""
from __future__ import annotations

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("explicit isolated CI Enterprise witness only")

import importlib.util
import os
import secrets
from pathlib import Path
import sys
import time
import types
import uuid

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

assert os.environ.get("HERMES_ENTERPRISE_RBAC_CANARY") == "synthetic-ci-only"
assert "NEO4J_URI" not in os.environ
assert "NEO4J_PASSWORD" not in os.environ
assert "NEO4J_AUTH" not in os.environ
assert os.environ.get("HERMES_RBAC_EPHEMERAL_ADMIN_PASSWORD")
URI="bolt://127.0.0.1:7687"
admin_pw=os.environ["HERMES_RBAC_EPHEMERAL_ADMIN_PASSWORD"]
assert len(admin_pw)>=20

src=Path(__file__).resolve().parents[1]/"src/assistx"
pkg=types.ModuleType("assistx")
pkg.__path__=[str(src)]
sys.modules["assistx"]=pkg
for name in ("hermes_claim_census","hermes_source_generation_research",
             "hermes_source_heartbeat_research","hermes_source_completion_research",
             "hermes_broker_command_research"):
    spec=importlib.util.spec_from_file_location("assistx."+name,src/(name+".py"))
    module=importlib.util.module_from_spec(spec)
    sys.modules["assistx."+name]=module
    spec.loader.exec_module(module)

from assistx import hermes_source_generation_research as ledger
from assistx import hermes_source_heartbeat_research as heartbeat
from assistx import hermes_source_completion_research as completion
from assistx import hermes_broker_command_research as broker

role="synthetic_generation_broker_role"
writer="synthetic_generation_broker"
reader_role="synthetic_generation_reader_role"
readers=("synthetic_generation_api","synthetic_generation_worker",
         "synthetic_generation_hermes")
writer_pw=secrets.token_urlsafe(32)
reader_pw=secrets.token_urlsafe(32)
prefix="SYNTHETIC_BROKER_LEDGER_"+uuid.uuid4().hex[:12]
task_id=prefix+"_task"
bypass_id=prefix+"_bypass"

admin=GraphDatabase.driver(URI,auth=("neo4j",admin_pw),connection_timeout=6)
drivers=[]
for count in range(75):
    try:
        admin.verify_connectivity()
        break
    except (Neo4jError,ServiceUnavailable,OSError):
        if count==74:raise
        time.sleep(1)

def system(query,**params):
    with admin.session(database="system") as s:
        return s.run(query,params).consume()

def data(query,**params):
    with admin.session(database="neo4j") as s:
        return s.run(query,params).single()

def new_driver(name,password):
    d=GraphDatabase.driver(URI,auth=(name,password),connection_timeout=6)
    d.verify_connectivity()
    drivers.append(d)
    return d

def task_state(tid):
    r=data("MATCH(t:Task{id:$id}) RETURN t.status AS status,t.lease_expires_at_ts AS expiry,"
           "t.claim_id AS claim_id,t.completed_by AS completed_by",id=tid)
    return dict(r) if r else None

def generation():
    return data("MATCH(g:HermesSourceGeneration{owner:'hermes-local'}) "
                "RETURN g.generation AS n")["n"]

def deny(d,query,tid,mark):
    try:
        with d.session(database="neo4j") as s:
            s.run(query,id=tid).consume()
    except Neo4jError as e:
        if e.code and e.code.startswith("Neo.ClientError.Security."):
            print("PASS "+mark,flush=True)
            return
        raise AssertionError("unexpected Neo4j error on denied query") from e
    raise AssertionError("privilege unexpectedly allowed: "+mark)

try:
    data("""
        CREATE CONSTRAINT hermes_source_generation_owner_unique IF NOT EXISTS
        FOR (g:HermesSourceGeneration) REQUIRE g.owner IS UNIQUE
    """)
    data("CREATE (g:HermesSourceGeneration{owner:'hermes-local',generation:0,lock_epoch:0}) "
         "RETURN g.generation AS generation")

    # Create property tokens up-front as the admin, so a custom role need
    # not be granted CREATE NEW PROPERTY NAME or CREATE NODE privileges.
    data("""
        CREATE (t:Task{id:$id,status:'RUNNING',claimed_by:'hermes-local',
              target_agent_id:'hermes-local',claim_id:'synthetic-claim',
              agent_session_id:'synthetic-session',execution_attempt:1,
              lease_expires_at_ts:timestamp()+120000,
              hermes_fence_check_epoch:0,heartbeat_by:'hermes-local',
              heartbeat_at_ts:0,failed_by:null,
              paused_from_agent_id:null,migration_target_agent_id:null})
        RETURN t.id AS id
    """,id=task_id)
    # Seed property tokens without marking the active claim complete.
    data("CREATE (t:Task{id:$id,status:'READY',hermes_fence_check_epoch:0,"
         "completed_by:'synthetic',completed_at:datetime(),completed_at_ts:0,"
         "title:'synthetic'}) RETURN t.id AS id",id=bypass_id)

    for name in readers+(writer,):
        system(f"CREATE USER {name} IF NOT EXISTS "
               "SET PASSWORD $pw CHANGE NOT REQUIRED",
               pw=writer_pw if name==writer else reader_pw)
    system(f"CREATE ROLE {reader_role} IF NOT EXISTS AS COPY OF reader")
    for name in readers:
        system(f"GRANT ROLE {reader_role} TO {name}")

    system(f"CREATE ROLE {role} IF NOT EXISTS")
    system(f"GRANT ACCESS ON DATABASE neo4j TO {role}")
    system(f"GRANT MATCH {{*}} ON GRAPH neo4j NODES * TO {role}")
    system(f"GRANT SHOW CONSTRAINT ON DATABASE neo4j TO {role}")
    system(
        f"GRANT SET PROPERTY "
        "{hermes_fence_check_epoch,status,completed_by,completed_at,completed_at_ts,"
        "heartbeat_by,heartbeat_at_ts,lease_expires_at_ts} "
        f"ON GRAPH neo4j NODES Task TO {role}"
    )
    system(f"GRANT SET PROPERTY {{lock_epoch,generation}} "
           f"ON GRAPH neo4j NODES HermesSourceGeneration TO {role}")
    system(f"GRANT ROLE {role} TO {writer}")

    for name in readers:
        d=new_driver(name,reader_pw)
        with d.session(database="neo4j") as s:
            state=s.run("MATCH(t:Task{id:$id}) RETURN t.status AS status",id=task_id).single()
        assert state["status"]=="RUNNING"
        deny(d,"MATCH(t:Task{id:$id}) SET t.status='DONE'",task_id,
             name+"_direct_Task_status_denied")
        deny(d,"MATCH(g:HermesSourceGeneration{owner:'hermes-local'}) "
             "SET g.generation=100",task_id,name+"_ledger_revision_denied")
    assert generation()==0 and task_state(task_id)["status"]=="RUNNING"
    print("PASS all_legacy_identities_read_and_write_denial",flush=True)

    d=new_driver(writer,writer_pw)
    heartbeat_command={
        "operation":"heartbeat", "expected_generation":0,
        "task_id":task_id,"claim_id":"synthetic-claim",
        "session_id":"synthetic-session","attempt":1,"ttl_ms":30000,
    }
    def candidate_session():
        return d.session(database="neo4j")
    renew=broker.dispatch_candidate_command(
        heartbeat_command, trusted_channel_principal="hermes-local",
        session_factory=candidate_session)
    assert renew["candidate_generation"]==1 and generation()==1
    assert renew["source_generation_writers_complete"] is False
    assert renew["production_dispatch_authorized"] is False
    print("PASS narrow_broker_heartbeat_and_generation_commit",flush=True)

    try:
        broker.dispatch_candidate_command(
            heartbeat_command, trusted_channel_principal="hermes-local",
            session_factory=candidate_session)
    except broker.CandidateBrokerCommandError:
        pass
    else:
        raise AssertionError("stale generation renewal unexpectedly accepted")
    assert generation()==1
    print("PASS narrow_broker_stale_replay_denied",flush=True)

    completion_command={
        "operation":"complete","expected_generation":1,
        "task_id":task_id,"claim_id":"synthetic-claim",
        "session_id":"synthetic-session","attempt":1,
    }
    completed=broker.dispatch_candidate_command(
        completion_command, trusted_channel_principal="hermes-local",
        session_factory=candidate_session)
    assert completed["candidate_generation"]==2 and generation()==2
    assert task_state(task_id)["status"]=="DONE"
    assert task_state(task_id)["completed_by"]=="hermes-local"
    assert completed["resource_side_completion_verified"] is False
    print("PASS narrow_broker_completion_and_generation_commit",flush=True)

    try:
        broker.dispatch_candidate_command(
            {**completion_command,"expected_generation":2},
            trusted_channel_principal="hermes-local",
            session_factory=candidate_session)
    except broker.CandidateBrokerCommandError:
        pass
    else:
        raise AssertionError("duplicate completion accepted")
    assert generation()==2
    print("PASS narrow_broker_duplicate_completion_denied",flush=True)

    deny(d,"MATCH(t:Task{id:$id}) SET t.title='unauthorized'",task_id,
         "narrow_broker_unlisted_Task_field_denied")
    deny(d,"MATCH(t:Task{id:$id}) DETACH DELETE t",task_id,
         "narrow_broker_Task_delete_denied")
    assert generation()==2 and task_state(task_id)["status"]=="DONE"
    # The interface must deny an attempted raw-Cypher command before any
    # privileged driver session is allocated by the broker's session factory.
    touched=[]
    def poison():
        touched.append(True)
        raise AssertionError("raw-query admission opened DB session")
    try:
        broker.dispatch_candidate_command(
            {"operation":"cypher","query":"synthetic-forbidden"},
            trusted_channel_principal="hermes-local",
            session_factory=poison)
    except broker.CandidateBrokerCommandError:
        pass
    else:
        raise AssertionError("unlisted command accepted")
    assert not touched
    print("PASS command_surface_raw_database_query_denied_before_session",flush=True)

    # Crucial fail-open witness. The legitimate write credential still has
    # freedom to bypass the application-level ledger. No DB ACL by itself
    # can prove a generation bound. This is a deliberate disposable test.
    with d.session(database="neo4j") as s:
        changed=s.run("MATCH(t:Task{id:$id}) SET t.status='DONE' "
                      "RETURN t.status AS status",id=bypass_id).single()
    assert changed["status"]=="DONE"
    assert generation()==2
    print("PASS broker_credential_still_bypasses_generation_PROTOCOL_HOLD",flush=True)

    print("HERMES_ENTERPRISE_GENERATION_BROKER_RESEARCH_PASS=10",flush=True)
    print("ALL_WRITERS_GENERATION_FENCED=FALSE;PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",
          flush=True)
finally:
    for d in drivers:d.close()
    try:
        data("MATCH(t:Task) WHERE t.id STARTS WITH $prefix DETACH DELETE t",prefix=prefix)
        data("MATCH(g:HermesSourceGeneration{owner:'hermes-local'}) DETACH DELETE g")
        data("DROP CONSTRAINT hermes_source_generation_owner_unique IF EXISTS")
        for name in readers+(writer,):
            system(f"DROP USER {name} IF EXISTS")
        system(f"DROP ROLE {reader_role} IF EXISTS")
        system(f"DROP ROLE {role} IF EXISTS")
    finally:
        admin.close()
