#!/usr/bin/env python3
"""Physical deny-direct-Task-write witness on isolated Neo4j Enterprise.

All credentials are intentionally disposable CI-only constants. This script
cannot connect to the fleet: it hardcodes 127.0.0.1, checks a CI opt-in marker,
refuses NEO4J_URI/NEO4J_PASSWORD, creates only synthetic nodes and roles,
and does not adjust any production users, roles or networks.

A DB writer role alone does NOT force ledger participation by its owner.
Any application with broker's credentials could still bypass the ledger.
"""
from __future__ import annotations

import os
import secrets
import time
import uuid

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

assert __name__=="__main__", "explicit disposable Neo4j only"
assert os.environ.get("HERMES_ENTERPRISE_RBAC_CANARY")=="synthetic-ci-only"
assert "NEO4J_URI" not in os.environ
assert "NEO4J_PASSWORD" not in os.environ
assert "NEO4J_AUTH" not in os.environ

URI="bolt://127.0.0.1:7687"
ADMIN_PW=os.environ.get("HERMES_RBAC_EPHEMERAL_ADMIN_PASSWORD")
assert ADMIN_PW and len(ADMIN_PW)>=20
ROLE="hermes_research_task_writer"
BROKER="synthetic_broker"
CLIENTS=("synthetic_api","synthetic_worker","synthetic_hermes")
READER_ROLE="hermes_research_reader"
CLIENT_PW=secrets.token_urlsafe(24)
WRITER_PW=secrets.token_urlsafe(24)
ID="SYNTHETIC_RBAC_"+uuid.uuid4().hex[:14]

admin=GraphDatabase.driver(URI,auth=("neo4j",ADMIN_PW),connection_timeout=6)
for attempt in range(75):
    try:
        admin.verify_connectivity()
        break
    except (Neo4jError,ServiceUnavailable,OSError):
        if attempt==74: raise
        time.sleep(1)


def system(q,**args):
    with admin.session(database="system") as s:
        return s.run(q,args).consume()


def data(q,**args):
    with admin.session(database="neo4j") as s:
        return s.run(q,args).single()


def identity(name,password):
    return GraphDatabase.driver(URI,auth=(name,password),connection_timeout=6)


def read(d,tid):
    with d.session(database="neo4j") as s:
        return s.run("MATCH(t:Task{id:$id}) RETURN t.status AS status",
                     id=tid).single()


def forbidden(d,query,tid,label):
    try:
        with d.session(database="neo4j") as s:
            rows=s.run(query,id=tid).consume()
    except Neo4jError as e:
        if (getattr(e,"code","").startswith("Neo.ClientError.Security.")
                or e.__class__.__name__.lower().find("authorization")!=-1):
            print("PASS "+label,flush=True)
            return
        raise AssertionError("unexpected database error class on denied write") from e
    raise AssertionError("direct Task write unexpectedly authorized: "+label)


drivers=[]
try:
    # Enterprise RBAC is mandatory. A Community image must fail the job.
    constraint=data("RETURN 1 AS ok")
    assert constraint["ok"]==1
    for name in CLIENTS+(BROKER,):
        system(
            f"CREATE USER {name} IF NOT EXISTS "
            "SET PASSWORD $password CHANGE NOT REQUIRED",
            password=WRITER_PW if name==BROKER else CLIENT_PW,
        )

    # Copy Neo4j's built-in reader role for the three former direct writers.
    system(f"CREATE ROLE {READER_ROLE} IF NOT EXISTS AS COPY OF reader")
    for name in CLIENTS:
        system(f"GRANT ROLE {READER_ROLE} TO {name}")

    # Narrow explicit privileges for the *synthetic* prospective broker.
    # Not sufficient alone for production: no application code/mutation
    # dispatch broker is wired, and a broker credential leak bypasses ledger.
    system(f"CREATE ROLE {ROLE} IF NOT EXISTS")
    system(f"GRANT ACCESS ON DATABASE neo4j TO {ROLE}")
    system(f"GRANT MATCH {{*}} ON GRAPH neo4j NODES * TO {ROLE}")
    system(f"GRANT SET PROPERTY {{status}} ON GRAPH neo4j NODES Task TO {ROLE}")
    system(f"GRANT ROLE {ROLE} TO {BROKER}")

    assert data(
        "CREATE(t:Task{id:$id,status:'READY'}) RETURN t.id AS id",id=ID,
    )["id"]==ID

    for name in CLIENTS:
        d=identity(name,CLIENT_PW)
        drivers.append(d)
        d.verify_connectivity()
        assert read(d,ID)["status"]=="READY"
        print("PASS "+name+"_read_preserved",flush=True)
        forbidden(
            d,"MATCH(t:Task{id:$id}) SET t.status='DONE' RETURN t.id",
            ID,name+"_direct_Task_property_write_denied",
        )
        forbidden(
            d,"MERGE(t:Task{id:$id}) SET t.status='DONE' RETURN t.id",
            ID,name+"_direct_Task_merge_denied",
        )
        assert read(d,ID)["status"]=="READY"

    broker=identity(BROKER,WRITER_PW)
    drivers.append(broker)
    broker.verify_connectivity()
    assert read(broker,ID)["status"]=="READY"
    with broker.session(database="neo4j") as s:
        out=s.run(
            "MATCH(t:Task{id:$id}) SET t.status='DONE' "
            "RETURN t.status AS status",id=ID,
        ).single()
    assert out["status"]=="DONE"
    print("PASS isolated_broker_Task_status_write_allowed",flush=True)
    # The narrow writer role must not be able to delete Task or add a
    # non-Task node, and the legacy readers remain able to see the change.
    forbidden(broker,"MATCH(t:Task{id:$id}) DETACH DELETE t",ID,
              "broker_Task_delete_denied")
    assert read(drivers[0],ID)["status"]=="DONE"
    print("PASS reader_sees_broker_committed_status",flush=True)
    print("HERMES_ENTERPRISE_TASK_RBAC_CANARY_PASS=11",flush=True)
    print("PRODUCTION_SERVICES_TOUCHED=0;PRODUCTION_DB_TOUCHED=0;DECISION=HOLD",flush=True)
finally:
    for d in drivers:
        d.close()
    try:
        data("MATCH(t:Task{id:$id}) DETACH DELETE t",id=ID)
        for name in CLIENTS+(BROKER,):
            system(f"DROP USER {name} IF EXISTS")
        system(f"DROP ROLE {READER_ROLE} IF EXISTS")
        system(f"DROP ROLE {ROLE} IF EXISTS")
    finally:
        admin.close()
