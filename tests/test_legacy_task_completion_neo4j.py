"""Real disposable Neo4j only. No production endpoint/credentials/task records."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from assistx.neo4j_client import Neo4jClient


@pytest.fixture(autouse=True)
def strict_legacy_research_mode(monkeypatch):
    monkeypatch.setenv("ASSISTX_ENABLE_STRICT_LEGACY_COMPLETION", "1")


@pytest.fixture(scope="module")
def client(neo4j_container):
    neo=Neo4jClient(uri=neo4j_container["uri"],user=neo4j_container["user"],
                   password=neo4j_container["password"],database=None)
    yield neo
    neo.close()


def seed(neo,*,rows=1,machine=False,expired=False,status="CLAIMED",
         agent="ordinary-worker",claim_id="ordinary-claim"):
    with neo._session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        for _ in range(rows):
            s.run("CREATE (:WorkSupplyState {name:'canonical',legacy_completion_serial:0})").consume()
        s.run("""
            CREATE (:Task {
                id:'synthetic-completion',
                status:$status,
                claimed_by:$agent,
                claim_id:$claim_id,
                agent_session_id:'synthetic-session',
                execution_attempt:1,
                lease_expires_at_ts:$expiry,
                machine_authorized:$machine,
                machine_authorization_generation:
                    CASE WHEN $machine THEN 17 ELSE null END,
                machine_token_epoch:
                    CASE WHEN $machine THEN 2 ELSE null END
            })
        """,{"status":status,"agent":agent,"claim_id":claim_id,
            "expiry":0 if expired else 9999999999999,"machine":machine}).consume()


def snapshot(neo):
    with neo._session() as s:
        task=s.run("MATCH (t:Task {id:'synthetic-completion'}) RETURN t").single()
        runs=s.run("MATCH (r:AgentRun) RETURN count(r) AS n").single()["n"]
        memories=s.run("MATCH (m:MemoryItem) RETURN count(m) AS n").single()["n"]
        serials=[r["serial"] for r in s.run(
            "MATCH (w:WorkSupplyState {name:'canonical'}) "
            "RETURN w.legacy_completion_serial AS serial")]
        return dict(task["t"]) if task else None,runs,memories,serials


def finish(neo,*,agent="ordinary-worker",claim="ordinary-claim",status="DONE",
           key="synthetic-completion-key",summary="synthetic verified outcome"):
    return neo.complete_task(
        "synthetic-completion",agent,status,
        claim_id=claim,session_id="synthetic-session",
        idempotency_key=key,summary=summary,
        result={"source":"synthetic"},
    )


def test_legitimate_ordinary_completion_and_exact_readonly_retry(client):
    seed(client)
    first=finish(client)
    assert first["status"]=="DONE"
    assert first["run_id"]
    assert first["memory_item_id"]
    before=snapshot(client)
    assert before[1:3]==(1,1)
    with client._session() as s:
        assert s.run("""
            MATCH (:Task {id:'synthetic-completion'})-[:RELATED_MEMORY]->(m:MemoryItem)
            RETURN count(m) AS n
        """).single()["n"]==1
    retry=finish(client)
    after=snapshot(client)
    assert retry["idempotent"] is True
    assert retry["run_id"]==first["run_id"]
    assert before==after, "exact retry must not rewrite Task/result/memory or lock serial"


@pytest.mark.parametrize("case",[
    "machine","missing-claim","wrong-claim","wrong-actor",
    "expired-lease","missing-lock","duplicate-lock",
    "targeted","partial-approval","hermes-scope",
    "different-session","attempt-zero","epoch-only","projection-only",
])
def test_unauthorized_completion_denied_without_task_or_memory_writes(client,case):
    seed(client,rows=0 if case=="missing-lock" else 2 if case=="duplicate-lock" else 1,
         machine=(case=="machine"),expired=(case=="expired-lease"))
    if case in {
        "targeted","partial-approval","hermes-scope",
        "different-session","attempt-zero","epoch-only","projection-only",
    }:
        property_assignments={
            "targeted":"t.target_agent_id='xwing'",
            "partial-approval":"t.machine_authorization_approval_id='synthetic-approval'",
            "hermes-scope":"t.hermes_claim_scope=true",
            "different-session":"t.agent_session_id='another-session'",
            "attempt-zero":"t.execution_attempt=0",
            "epoch-only":"t.machine_token_epoch=3",
            "projection-only":"t.projection_generation=17",
        }
        with client._session() as session:
            session.run(
                "MATCH (t:Task {id:'synthetic-completion'}) SET "
                +property_assignments[case]
            ).consume()
    before=snapshot(client)
    result=finish(client,
        agent="wrong-actor" if case=="wrong-actor" else "ordinary-worker",
        claim=None if case=="missing-claim" else "wrong-claim" if case=="wrong-claim" else "ordinary-claim",
    )
    after=snapshot(client)
    assert result is None,case
    assert before==after,case


def test_terminal_rewrite_and_wrong_actor_idempotent_replay_denied(client):
    seed(client)
    first=finish(client,status="FAILED",key="fixed-completion")
    assert first["status"]=="FAILED"
    before=snapshot(client)
    assert finish(client,status="DONE",key="fixed-completion") is None
    assert finish(client,agent="other-worker",key="fixed-completion") is None
    assert finish(client,claim="old-claim",key="fixed-completion") is None
    assert finish(client,status="FAILED",key="fixed-completion",
                  summary="different payload") is None
    assert snapshot(client)==before


def test_lease_change_after_waiting_for_lock_fails_closed(client):
    seed(client)
    with client._session() as s:
        tx=s.begin_transaction()
        try:
            tx.run("""
                MATCH (w:WorkSupplyState {name:'canonical'})
                SET w.synthetic_hold=1
                RETURN w
            """).consume()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(finish,client)
                try:
                    with pytest.raises(TimeoutError):
                        pending.result(timeout=0.3)
                finally:
                    tx.run("""
                      MATCH (t:Task {id:'synthetic-completion'})
                      SET t.machine_authorized=true,
                          t.machine_authorization_generation=17,
                          t.machine_token_epoch=2
                    """).consume()
                    tx.commit()
                assert pending.result(timeout=18) is None
        finally:
            if not tx.closed():
                tx.rollback()
    task,runs,memories,_=snapshot(client)
    assert task["status"]=="CLAIMED"
    assert task["machine_authorized"] is True
    assert (runs,memories)==(0,0)
