"""Real disposable Neo4j research tests; no production graph or API."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier

import pytest
from neo4j import GraphDatabase

from assistx.operator_review_transition import (
    ReviewTransitionDenied, propose_operator_review_transition,
)

BASE_REVISION=1700000000123


@pytest.fixture(scope="module")
def driver(neo4j_container):
    d=GraphDatabase.driver(
        neo4j_container["uri"],
        auth=(neo4j_container["user"],neo4j_container["password"]),
    )
    yield d
    d.close()


class Neo:
    def __init__(self,driver):
        self.driver=driver
    def _session(self):
        return self.driver.session()


def seed(driver,*,coordination=1,duplicates=1,state="REVIEW",extra=None):
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        for _ in range(coordination):
            s.run(
                "CREATE (:WorkSupplyState {name:'canonical',operator_review_serial:0})"
            ).consume()
        for _ in range(duplicates):
            s.run("""
                CREATE (:Task {
                    id:'synthetic-review',
                    status:$status,
                    kind:'summarized_action',
                    created_at_ts:1699999999000,
                    updated_at_ts:$revision,
                    execution_attempt:0,
                    title:'Synthetic research Task'
                })
            """,{"status":state,"revision":BASE_REVISION}).consume()
        if extra:
            s.run("MATCH (t:Task {id:'synthetic-review'}) SET "+extra).consume()


def approve(driver,*,actor="synthetic-operator",state="REVIEW",revision=BASE_REVISION):
    return propose_operator_review_transition(
        Neo(driver),task_id="synthetic-review",
        actor=actor,expected_status=state,
        expected_updated_at_ts=revision,
    )


def snapshot(driver):
    with driver.session() as s:
        task=[dict(r["t"]) for r in s.run(
            "MATCH (t:Task {id:'synthetic-review'}) RETURN t"
        )]
        serials=[r["v"] for r in s.run(
            "MATCH (w:WorkSupplyState {name:'canonical'}) "
            "RETURN w.operator_review_serial AS v"
        )]
        return task,serials


@pytest.mark.parametrize("state",["REVIEW","PROPOSED"])
def test_review_to_ready_uses_exact_revision_records_actor(state,driver):
    seed(driver,state=state)
    result=approve(driver,state=state)
    assert result["task_id"]=="synthetic-review"
    assert result["status"]=="READY"
    assert result["approved_by"]=="synthetic-operator"
    assert result["review_version"]==1
    tasks,serials=snapshot(driver)
    assert len(tasks)==1
    assert tasks[0]["status"]=="READY"
    assert tasks[0]["approved_by"]=="synthetic-operator"
    assert tasks[0]["operator_review_source"]==state
    assert tasks[0]["updated_at_ts"]!=BASE_REVISION
    assert serials==[1]
    with pytest.raises(ReviewTransitionDenied):
        approve(driver,state=state)
    assert snapshot(driver)==(tasks,serials)


@pytest.mark.parametrize("case",[
    "stale-revision","wrong-state","already-ready",
    "machine","generation","approval","token-epoch","projection",
    "targeted","claim","claimed-by","session","lease","attempt",
    "hermes","intent-review","already-approved","review-decision",
    "paused","auto-dispatch","dispatch-edge","allocation-edge",
    "duplicate-id","missing-lock","duplicate-lock",
])
def test_unqualified_review_cannot_be_promoted(case,driver):
    seed(driver,duplicates=2 if case=="duplicate-id" else 1,
         coordination=0 if case=="missing-lock" else 2 if case=="duplicate-lock" else 1,
         state="READY" if case=="already-ready" else "REVIEW")
    properties={
       "machine":"t.machine_authorized=true",
       "generation":"t.machine_authorization_generation=17",
       "approval":"t.machine_authorization_approval_id='synthetic-approval'",
       "token-epoch":"t.machine_token_epoch=2",
       "projection":"t.projection_generation=18",
       "targeted":"t.target_agent_id='xwing'",
       "claim":"t.claim_id='old-claim'",
       "claimed-by":"t.claimed_by='old-worker'",
       "session":"t.agent_session_id='old-session'",
       "lease":"t.lease_expires_at_ts=9999999999999",
       "attempt":"t.execution_attempt=1",
       "hermes":"t.hermes_claim_scope=true",
       "intent-review":"t.kind='intent_review'",
       "already-approved":"t.approved_by='previous-reviewer'",
       "review-decision":"t.review_decision='approved'",
       "paused":"t.paused=true",
       "auto-dispatch":"t.auto_dispatch=true",
    }
    with driver.session() as s:
        if case in properties:
            s.run(
                "MATCH (t:Task {id:'synthetic-review'}) SET "+properties[case]
            ).consume()
        if case=="dispatch-edge":
            s.run("""
                MATCH (t:Task {id:'synthetic-review'})
                CREATE (t)-[:DISPATCHED_AS]->(:Dispatch {id:'synthetic'})
            """).consume()
        if case=="allocation-edge":
            s.run("""
                MATCH (t:Task {id:'synthetic-review'})
                CREATE (t)-[:HAS_ALLOCATION]->(:AllocationReservation {id:'synthetic'})
            """).consume()
    before=snapshot(driver)
    with pytest.raises(ReviewTransitionDenied):
        approve(driver,revision=BASE_REVISION-1 if case=="stale-revision"
                else BASE_REVISION,
                state="PROPOSED" if case=="wrong-state" else "REVIEW")
    assert snapshot(driver)==before,case


def test_concurrent_same_version_approvals_exactly_one_winner(driver):
    seed(driver)
    gate=Barrier(2)
    def attempt(actor):
        gate.wait(timeout=10)
        try:
            return approve(driver,actor=actor)
        except ReviewTransitionDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        x=pool.submit(attempt,"operator-A")
        y=pool.submit(attempt,"operator-B")
        results=[x.result(timeout=18),y.result(timeout=18)]
    assert len([r for r in results if r is not None])==1
    tasks,serials=snapshot(driver)
    assert tasks[0]["approved_by"] in {"operator-A","operator-B"}
    assert tasks[0]["status"]=="READY"
    assert serials==[1]


def test_post_lock_machine_protection_change_blocks_stale_approval(driver):
    seed(driver)
    with driver.session() as s:
        tx=s.begin_transaction()
        try:
            tx.run("""
                MATCH (w:WorkSupplyState {name:'canonical'})
                SET w.synthetic_test_lock=1
            """).consume()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(approve,driver)
                try:
                    with pytest.raises(TimeoutError):
                        future.result(timeout=0.3)
                finally:
                    tx.run("""
                        MATCH (t:Task {id:'synthetic-review'})
                        SET t.machine_authorized=true,
                            t.machine_token_epoch=2,
                            t.machine_authorization_generation=17
                    """).consume()
                    tx.commit()
                with pytest.raises(ReviewTransitionDenied):
                    future.result(timeout=20)
        finally:
            if not tx.closed():
                tx.rollback()
    tasks,serials=snapshot(driver)
    assert tasks[0]["status"]=="REVIEW"
    assert tasks[0]["machine_authorized"] is True
    assert serials==[0]
