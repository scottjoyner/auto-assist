#!/usr/bin/env python3
"""Exact staged Cypher vs disposable no-network Neo4j; TEST DATA ONLY."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from test_hermes_db_claim_fence import load, Session

CONTAINER=os.environ.get("HERMES_DISPOSABLE_NEO4J", "")
if not CONTAINER.startswith("hermes-db-only-"):
    raise SystemExit("HOLD: disposable Neo4j identity missing")
Neo4jClient=load()


def cypher_value(val):
    if isinstance(val,dict):
        return "{"+", ".join(f"{k}: {cypher_value(v)}" for k,v in val.items())+"}"
    if isinstance(val,list):
        return "["+", ".join(cypher_value(v) for v in val)+"]"
    if val is None:
        return "null"
    if val is True:
        return "true"
    if val is False:
        return "false"
    if isinstance(val,(int,float)):
        return str(val)
    if isinstance(val,str):
        return json.dumps(val)
    raise ValueError("unsupported fixture type")


def command(query, params=None):
    args=["docker","exec",CONTAINER,"cypher-shell",
          "-a","bolt://127.0.0.1:7687","--format","plain"]
    if params:
        args.extend(["-P",cypher_value(params)])
    args.append(query)
    proc=subprocess.run(args,capture_output=True,text=True,
                        timeout=18,check=False)
    if proc.returncode:
        raise RuntimeError("SYNTHETIC_QUERY_FAILED:"+str(proc.stderr)[-230:])
    return proc.stdout.strip()


def query_for(op, *, claim_id="good", session_id="session"):
    session=Session()
    client=object.__new__(Neo4jClient)
    client._session=lambda:session
    if op=="heartbeat":
        client.heartbeat_task("placeholder","hermes-local",
                              status="RUNNING",claim_id=claim_id,
                              session_id=session_id,lease_seconds=60)
    elif op=="complete":
        client.complete_task("placeholder","hermes-local","DONE",
                             claim_id=claim_id,session_id=session_id,
                             summary=None)
    else:
        raise ValueError("bad operation")
    assert len(session.queries)==1
    return session.queries[0]


def trial(index, op, *, expected, owner="hermes-local", claim="good",
          session_id="session", status="CLAIMED", attempt=1,
          expires_delta=120000, provided_claim="good",
          provided_session="session"):
    task=f"synthetic-hermes-fence-{index}"
    fixture={
        "task_id":task,"status":status,"claim_id":claim,
        "owner":owner,"agent_session":session_id,
        "attempt":attempt,"delta":expires_delta,
    }
    command(
        "CREATE (t:Task {id:$task_id,status:$status,claim_id:$claim_id,"
        "claimed_by:$owner,agent_session_id:$agent_session,"
        "execution_attempt:$attempt,lease_expires_at_ts:timestamp()+$delta})",
        fixture,
    )
    stmt,p=query_for(op,claim_id=provided_claim,session_id=provided_session)
    p["task_id"]=task
    command(stmt,p)
    if op=="heartbeat":
        witness=command(
            "MATCH (t:Task{id:$task_id}) RETURN CASE WHEN "
            "((t.status='RUNNING') AND t.heartbeat_by='hermes-local') "
            "THEN 'ACCEPTED' ELSE 'REJECTED' END AS outcome",
            {"task_id":task},
        )
    else:
        witness=command(
            "MATCH (t:Task{id:$task_id}) "
            "OPTIONAL MATCH (t)-[:EXECUTED_BY]->(r:AgentRun) "
            "WITH t, count(r) AS runs "
            "RETURN CASE WHEN (t.status='DONE' AND runs=1) "
            "THEN 'ACCEPTED' ELSE 'REJECTED' END AS outcome",
            {"task_id":task},
        )
    found="ACCEPTED" if "ACCEPTED" in witness else "REJECTED"
    assert found==expected,(index,op,expected,witness)
    print(f"TRIAL_{index}={op}:{found}")


trial("valid-heartbeat","heartbeat",expected="ACCEPTED")
trial("stale-heartbeat","heartbeat",expected="REJECTED",provided_claim="old")
trial("wrong-session-heartbeat","heartbeat",expected="REJECTED",
      provided_session="wrong")
trial("expired-heartbeat","heartbeat",expected="REJECTED",expires_delta=-10000)
trial("revoked-heartbeat","heartbeat",expected="REJECTED",status="READY")
trial("zero-attempt-heartbeat","heartbeat",expected="REJECTED",attempt=0)
trial("valid-complete","complete",expected="ACCEPTED")
trial("stale-complete","complete",expected="REJECTED",provided_claim="old")
trial("wrong-session-complete","complete",expected="REJECTED",
      provided_session="wrong")
trial("expired-complete","complete",expected="REJECTED",expires_delta=-10000)
trial("revoked-complete","complete",expected="REJECTED",status="CANCELLED")
trial("zero-attempt-complete","complete",expected="REJECTED",attempt=0)
trial("wrong-owner-complete","complete",expected="REJECTED",owner="different")

print("SYNTHETIC_NEO4J_TRIALS_PASS=13")
print("REAL_TASKS_TOUCHED=0")
print("PRODUCTION_AUTHORIZED=false")
