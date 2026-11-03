"""Real disposable Neo4j strict-heartbeat and unattributed status-denial tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from assistx.neo4j_client import Neo4jClient
from assistx.legacy_task_writer_guard import LegacyWriterDenied


@pytest.fixture(scope="module")
def neo(neo4j_container):
    c=Neo4jClient(uri=neo4j_container["uri"],user=neo4j_container["user"],
        password=neo4j_container["password"],database=None)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def strict_mode(monkeypatch):
    monkeypatch.setenv("ASSISTX_ENABLE_STRICT_LEGACY_HEARTBEAT","1")
    monkeypatch.setenv("ASSISTX_ENABLE_STRICT_UNATTRIBUTED_TASK_STATUS","1")


def seed(neo, *, count=1, protected=False, task_duplicates=1, expired=False,
         state="CLAIMED", extra=None):
    with neo._session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        for _ in range(count):
            s.run("CREATE (:WorkSupplyState {name:'canonical',legacy_heartbeat_serial:0})").consume()
        for _ in range(task_duplicates):
            s.run("""
              CREATE (t:Task {
                id:'synthetic-hb',
                status:$state,
                claimed_by:'ordinary-worker',
                claim_id:'current-claim',
                agent_session_id:'claimed-session',
                execution_attempt:1,
                lease_seconds:120,
                lease_expires_at_ts:$expires,
                machine_authorized:$protected
              })
            """,{"state":state,"expires":0 if expired else 9999999999999,
                  "protected":protected}).consume()
        if extra:
            s.run("MATCH (t:Task {id:'synthetic-hb'}) SET "+extra).consume()


def view(neo):
    with neo._session() as s:
        task=[dict(r["t"]) for r in s.run("MATCH (t:Task {id:'synthetic-hb'}) RETURN t")]
        serial=[r["n"] for r in s.run(
            "MATCH (w:WorkSupplyState {name:'canonical'}) RETURN w.legacy_heartbeat_serial AS n")]
        return task,serial


def heartbeat(neo,*,agent="ordinary-worker",claim="current-claim",
              session="claimed-session",status="RUNNING",lease=60):
    return neo.heartbeat_task("synthetic-hb",agent,status=status,
        session_id=session,claim_id=claim,lease_seconds=lease,
        metadata={"source":"synthetic"})


def test_valid_ordinary_heartbeat_and_unattributed_status_blocked(neo):
    seed(neo)
    hb=heartbeat(neo)
    assert hb["status"]=="RUNNING"
    assert hb["agent_session_id"]=="claimed-session"
    assert hb["heartbeat_by"]=="ordinary-worker"
    assert hb["heartbeat_metadata_json"]=='{"source": "synthetic"}'
    before=view(neo)
    with pytest.raises(LegacyWriterDenied,match="unattributed_direct"):
        neo.update_task_status("synthetic-hb","DONE")
    assert view(neo)==before


@pytest.mark.parametrize("case",[
 "machine","generation","approval","epoch","projection","targeted",
 "hermes","wrong-actor","wrong-claim","wrong-session",
 "expired","missing-lock","duplicate-lock","duplicate-Task","terminal",
 "attempt-zero",
])
def test_rejected_heartbeat_is_readonly_on_task_and_lock(neo,case):
    seed(neo,
         count=0 if case=="missing-lock" else 2 if case=="duplicate-lock" else 1,
         protected=case=="machine",
         task_duplicates=2 if case=="duplicate-Task" else 1,
         expired=case=="expired",
         state="DONE" if case=="terminal" else "CLAIMED",
    )
    mutations={
       "generation":"t.machine_authorization_generation=17",
       "approval":"t.machine_authorization_approval_id='synthetic-approval'",
       "epoch":"t.machine_token_epoch=2",
       "projection":"t.projection_generation=17",
       "targeted":"t.target_agent_id='xwing'",
       "hermes":"t.hermes_claim_scope=true",
       "attempt-zero":"t.execution_attempt=0",
    }
    if case in mutations:
        with neo._session() as s:
            s.run("MATCH (t:Task {id:'synthetic-hb'}) SET "+mutations[case]).consume()
    before=view(neo)
    assert heartbeat(
        neo,
        agent="wrong-worker" if case=="wrong-actor" else "ordinary-worker",
        claim="stale-claim" if case=="wrong-claim" else "current-claim",
        session="wrong-session" if case=="wrong-session" else "claimed-session",
    ) is None
    assert view(neo)==before,case


def test_expiring_or_revoking_claim_while_lock_held_denies_heartbeat(neo):
    seed(neo)
    with neo._session() as s:
        tx=s.begin_transaction()
        try:
            tx.run("""
                MATCH (w:WorkSupplyState {name:'canonical'})
                SET w.synthetic_hold=1
            """).consume()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(heartbeat,neo)
                try:
                    with pytest.raises(TimeoutError):
                        pending.result(timeout=0.3)
                finally:
                    tx.run("""
                        MATCH (t:Task {id:'synthetic-hb'})
                        SET t.machine_authorized=true,
                            t.machine_authorization_generation=17,
                            t.machine_token_epoch=2
                    """).consume()
                    tx.commit()
                assert pending.result(timeout=20) is None
        finally:
            if not tx.closed():
                tx.rollback()
    task,serial=view(neo)
    assert len(task)==1
    assert task[0]["status"]=="CLAIMED"
    assert task[0]["machine_authorized"] is True
    assert serial==[0]


def test_pausing_never_reopened_by_legacy_heartbeat(neo):
    seed(neo,state="PAUSING")
    result=heartbeat(neo,status="RUNNING")
    assert result["status"]=="PAUSING"
    assert view(neo)[0][0]["status"]=="PAUSING"


def test_default_legacy_compatibility_unmodified_when_optins_off(neo,monkeypatch):
    seed(neo)
    monkeypatch.delenv("ASSISTX_ENABLE_STRICT_LEGACY_HEARTBEAT",raising=False)
    monkeypatch.delenv("ASSISTX_ENABLE_STRICT_UNATTRIBUTED_TASK_STATUS",raising=False)
    # Preserve legacy behavior: it remains UNSAFE until owner-approved migration.
    assert neo.heartbeat_task(
        "synthetic-hb","ordinary-worker",status="RUNNING",claim_id=None,
    )["status"]=="RUNNING"
    neo.update_task_status("synthetic-hb","DONE")
    assert view(neo)[0][0]["status"]=="DONE"
