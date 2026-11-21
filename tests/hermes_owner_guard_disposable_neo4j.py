#!/usr/bin/env python3
"""Synthetic real-Neo4j Hermes Task owner-guard canary; never production.

Run directly under GitHub Actions with an ephemeral localhost Neo4j service.
Pytest collection skips this file. Uses no production credentials, network
address, private corpus, or real fleet workload.
"""
from __future__ import annotations

if __name__ != "__main__":
    import unittest
    raise unittest.SkipTest("disposable localhost Neo4j only; run explicitly")

import ast
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import time
import uuid

from neo4j import GraphDatabase

assert os.environ.get("HERMES_DISPOSABLE_NEO4J") == "synthetic-ci-only", (
    "explicit disposable CI qualification required"
)
assert not os.environ.get("NEO4J_URI"), "must never read a production URI"

SRC = Path(__file__).resolve().parents[1] / "src/assistx"
driver = GraphDatabase.driver(
    "bolt://127.0.0.1:7687", auth=None, connection_timeout=5,
    max_transaction_retry_time=10,
)
for i in range(75):
    try:
        driver.verify_connectivity()
        break
    except Exception:
        if i == 74:
            raise
        time.sleep(1)


def query(module, func):
    tree = ast.parse((SRC/module).read_text(encoding="utf-8"))
    functions = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == func]
    assert len(functions) == 1, func
    found = [n.args[0].value for n in ast.walk(functions[0])
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "run"
             and n.args and isinstance(n.args[0], ast.Constant)
             and isinstance(n.args[0].value, str)]
    assert len(found) == 1, (module, func)
    return found[0]


def call(sql, **params):
    with driver.session(database="neo4j") as session:
        return session.run(sql, params).single()


def rows(sql, **params):
    with driver.session(database="neo4j") as session:
        return [dict(row) for row in session.run(sql, params)]


PFX = "SYNTHETIC_HERMES_WRITER_" + uuid.uuid4().hex[:8]


def task(label, **properties):
    tid = PFX + "_" + label
    assert call(
        "CREATE (t:Task {id:$id}) SET t += $props RETURN t.id AS id",
        id=tid,
        props={"status":"READY",**properties},
    )["id"] == tid
    return tid


def state(tid):
    return dict(call("MATCH (t:Task {id:$id}) RETURN t", id=tid)["t"])


def denied(row, label):
    assert row is None, (label,row)
    print("PASS " + label, flush=True)


try:
    pause = query("api.py", "api_pause_task")
    resume = query("api.py", "api_resume_task")
    normal = task("api_normal", claimed_by="worker-1")
    protected = task("api_hermes", claimed_by="hermes-local")
    denied(call(pause, id=protected), "api_hermes_pause_rejected")
    assert state(protected).get("paused") is None
    assert call(pause, id=normal)["updated_id"] == normal
    assert state(normal)["paused"] is True
    assert call(resume, id=normal)["updated_id"] == normal
    assert state(normal)["paused"] is False
    denied(call(resume, id=protected), "api_hermes_resume_rejected")
    print("PASS api_nonhermes_pause_and_resume", flush=True)

    # Lock a Task and reissue it to Hermes in a different transaction.
    # The competing generic pause must block, then recheck after commit.
    raced = task("api_lock_race", claimed_by="worker-1")
    with driver.session(database="neo4j") as sa:
        tx = sa.begin_transaction()
        try:
            assert tx.run(
                "MATCH (t:Task{id:$id}) SET t.claimed_by='hermes-local' "
                "RETURN t.id AS id", id=raced,
            ).single() is not None
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(call, pause, id=raced)
                time.sleep(0.25)
                assert not future.done(), "pause bypassed a live Task write lock"
                tx.commit()
                denied(future.result(timeout=25), "api_race_after_hermes_reissue")
        finally:
            if not tx.closed():
                tx.rollback()
    assert state(raced).get("paused") is None

    voice = query("voice_routes.py", "_cancel_target_task")
    vt = task("voice_hermes", status="RUNNING", claimed_by="hermes-local")
    assert call(voice, task_id=vt, reason="synthetic cancel")["cancelled"] == 1
    assert state(vt)["status"] == "CANCELLED"
    done = task("voice_done", status="DONE")
    assert call(voice, task_id=done, reason="synthetic cancel")["cancelled"] == 0
    assert state(done)["status"] == "DONE"
    print("PASS voice_cancel_still_works_and_no_terminal_regression",flush=True)

    intent = query("intent_orchestrator.py", "_handle_cancel")
    it = task("intent_hermes", status="RUNNING", claimed_by="hermes-local")
    call(
        "CREATE (i:Intent{id:$iid}) WITH i "
        "MATCH (t:Task{id:$tid}) MERGE (i)-[:CREATED_TASK]->(t) "
        "RETURN i.id AS id",
        iid=PFX+"_intent", tid=it,
    )
    with driver.session(database="neo4j") as s:
        changed = s.run(intent, {"id":PFX+"_intent","reason":"synthetic"}).data()
    assert len(changed)==1 and state(it)["status"]=="CANCELLED"
    print("PASS intent_cancel_hermes_safety_available",flush=True)

    claimed = query("passive_claims.py", "_tx_claim_passive_task")
    current = int(time.time()*1000)
    args = {
        "claim_id": PFX+"_claim", "agent_id":"passive-reviewer",
        "lease_id": PFX+"_lease", "mode":"review_only",
        "now_ms":current, "expires_at_ts":current+120000,
        "metadata_json":"{}",
    }
    ready = task("passive_normal")
    assert call(claimed, task_id=ready, **args) is not None
    assert state(ready)["status"]=="CLAIMED_PASSIVE"
    protected_ready = task("passive_hermes", claimed_by="hermes-local")
    denied(call(claimed, task_id=protected_ready, **args),
           "passive_claim_existing_hermes_rejected")
    agent_ready = task("passive_actor")
    denied(call(claimed, task_id=agent_ready,
                **{**args,"agent_id":"hermes-local"}),
           "passive_claim_hermes_actor_rejected")

    renew = query("passive_claims.py", "_tx_renew_passive_claim")
    assert call(
        renew, task_id=ready, claim_id=args["claim_id"],
        agent_id=args["agent_id"], now_ms=current,
        expires_at_ts=current+180000,
        progress_note="synthetic", metadata_json="{}",
    ) is not None

    release = query("passive_claims.py", "_tx_release_passive_claim")
    assert call(
        release, task_id=ready, claim_id=args["claim_id"],
        agent_id=args["agent_id"], result="released",
        summary="synthetic", next_status="READY",
        now_ms=current, metadata_json="{}",
    ) is not None
    assert state(ready)["status"]=="READY"
    print("PASS passive_normal_claim_renew_release",flush=True)

    expired = query("passive_claims.py", "_tx_expire_passive_claims")
    normal_expired = task(
        "passive_expired", status="CLAIMED_PASSIVE",
        passive_claim_id=PFX+"_expired", passive_claim_agent_id="passive-reviewer",
        passive_claim_expires_at_ts=current-10000, previous_status="READY",
    )
    hermes_expired = task(
        "passive_expired_hermes", status="CLAIMED_PASSIVE",
        passive_claim_id=PFX+"_expired_h",passive_claim_agent_id="hermes-local",
        passive_claim_expires_at_ts=current-10000, previous_status="READY",
    )
    out=rows(expired, now_ms=current, limit=50)
    assert normal_expired in [row["task_id"] for row in out]
    assert hermes_expired not in [row["task_id"] for row in out]
    assert state(normal_expired)["status"]=="READY"
    assert state(hermes_expired)["status"]=="CLAIMED_PASSIVE"
    print("PASS passive_expiry_rejects_hermes",flush=True)

    rollback = query("passive_claims.py", "_rollback_claim")
    rb = task("passive_rollback", status="CLAIMED_PASSIVE",
              passive_claim_id=PFX+"_rollback",
              passive_claim_agent_id="passive-reviewer",
              previous_status="REVIEW")
    assert call(rollback, task_id=rb, claim_id=PFX+"_rollback",
                reason="synthetic") is not None
    assert state(rb)["status"]=="REVIEW"
    rb_protected = task("passive_rollback_hermes",
                        status="CLAIMED_PASSIVE",
                        passive_claim_id=PFX+"_rollback_h",
                        passive_claim_agent_id="hermes-local")
    denied(call(rollback, task_id=rb_protected,
                claim_id=PFX+"_rollback_h",reason="synthetic"),
           "passive_rollback_hermes_rejected")
    print("PASS passive_normal_rollback",flush=True)

    print("SYNTHETIC_HERMES_OWNER_GUARD_DB_PASS=1",flush=True)
    print("PRODUCTION_TASKS_TOUCHED=0;DECISION=HOLD",flush=True)
finally:
    driver.close()
