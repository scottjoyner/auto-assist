"""Real disposable Neo4j tests; all synthetic, never use production graph."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from hashlib import sha256
from threading import Barrier

import pytest
from neo4j import GraphDatabase

from assistx import review_atomic_approval as research


REVISION = 1700000012345
REVIEW_ID = "synthetic-review-atomic"


@pytest.fixture(scope="module")
def driver(neo4j_container):
    d = GraphDatabase.driver(
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


def candidate_id():
    return "held-review-task-" + sha256(REVIEW_ID.encode()).hexdigest()


def outbox_id():
    return "held-review-outbox-" + sha256(REVIEW_ID.encode()).hexdigest()


def seed(driver, *, locks=1, reviews=1, state="REVIEW", extra=None, payload=None):
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        for _ in range(locks):
            s.run(
                "CREATE (:WorkSupplyState {name:'canonical',review_outbox_serial:0})"
            ).consume()
        for _ in range(reviews):
            s.run("""
                CREATE (:Task {
                    id:$review_id,
                    status:$status,
                    kind:'intent_review',
                    title:'Synthetic original review',
                    priority:'HIGH',
                    payload_json:$payload,
                    updated_at_ts:$revision,
                    execution_attempt:0
                })
            """,{
                "review_id":REVIEW_ID,
                "status":state,
                "revision":REVISION,
                "payload":payload if payload is not None
                    else '{"source_text":"Synthetic approved work","source_intent":"intent-1"}',
            }).consume()
        if extra:
            s.run("MATCH (r:Task {id:$id}) SET "+extra,{"id":REVIEW_ID}).consume()


def approve(driver, *, actor="local-research-operator", revision=REVISION, dispatch=False):
    return research.propose_held_review_approval(
        Neo(driver), review_id=REVIEW_ID, actor=actor,
        expected_revision=revision, note="Synthetic approval",
        auto_dispatch=dispatch,
    )


def snapshot(driver):
    with driver.session() as s:
        reviews=[dict(r["n"]) for r in s.run(
            "MATCH (n:Task {id:$id}) RETURN n",{"id":REVIEW_ID}
        )]
        children=[dict(r["n"]) for r in s.run(
            "MATCH (n:Task {approved_from_review_task:$id}) RETURN n",
            {"id":REVIEW_ID}
        )]
        outboxes=[dict(r["o"]) for r in s.run(
            "MATCH (o:ReviewApprovalOutbox {review_task_id:$id}) RETURN o",
            {"id":REVIEW_ID}
        )]
        links=s.run("""
            MATCH (r:Task {id:$id})
            OPTIONAL MATCH (r)-[link:APPROVED_AS]->(:Task)
            RETURN count(link) AS n
        """,{"id":REVIEW_ID}).single()["n"]
        serials=[row["s"] for row in s.run(
            "MATCH (w:WorkSupplyState {name:'canonical'}) "
            "RETURN w.review_outbox_serial AS s"
        )]
        dispatch_count=s.run(
            "MATCH (d:Dispatch) RETURN count(d) AS n"
        ).single()["n"]
    return reviews,children,outboxes,links,serials,dispatch_count


def test_atomic_approved_held_candidate_and_exact_retry_does_not_duplicate(driver):
    seed(driver)
    result=approve(driver)
    assert result["review_task_id"]==REVIEW_ID
    assert result["candidate_task_id"]==candidate_id()
    assert result["candidate_status"]=="PENDING_ADMISSION"
    assert result["outbox_id"]==outbox_id()
    assert result["outbox_status"]=="HELD_UNTIL_ADMISSION"
    assert result["decision"]=="approved_held"
    reviews,children,outboxes,links,serials,dispatch=snapshot(driver)
    assert len(reviews)==len(children)==len(outboxes)==1
    assert reviews[0]["status"]=="DONE"
    assert reviews[0]["review_decision"]=="approved_held"
    assert children[0]["status"]=="PENDING_ADMISSION"
    assert children[0]["admission_verified"] is False
    assert children[0]["auto_dispatch"] is False
    assert children[0]["title"]=="Synthetic approved work"
    assert outboxes[0]["status"]=="HELD_UNTIL_ADMISSION"
    assert outboxes[0]["signed_admission"] is False
    assert links==1 and serials==[1] and dispatch==0
    before=snapshot(driver)
    with pytest.raises(research.ReviewAtomicApprovalDenied):
        approve(driver)
    assert snapshot(driver)==before


@pytest.mark.parametrize("case",[
    "stale-revision","already-done","kind","decision","reviewed-by",
    "reviewed-at","claim-id","claimed-by","session","lease","attempt",
    "target","machine","machine-gen","machine-approval","token-epoch",
    "projection","hermes","auto-dispatch","approved-link",
    "candidate-id","candidate-origin","outbox-id",
    "missing-lock","duplicate-lock","duplicate-review",
    "malformed-payload",
])
def test_protected_stale_or_duplicate_review_aborts_without_writes(driver,case):
    seed(
        driver,
        locks=0 if case=="missing-lock" else 2 if case=="duplicate-lock" else 1,
        reviews=2 if case=="duplicate-review" else 1,
        state="DONE" if case=="already-done" else "REVIEW",
        payload="broken non json" if case=="malformed-payload" else None,
    )
    mutations={
        "kind":"r.kind='other_task'",
        "decision":"r.review_decision='approved'",
        "reviewed-by":"r.reviewed_by='other'",
        "reviewed-at":"r.reviewed_at_ts=123",
        "claim-id":"r.claim_id='claim'",
        "claimed-by":"r.claimed_by='worker'",
        "session":"r.agent_session_id='session'",
        "lease":"r.lease_expires_at_ts=9999999999999",
        "attempt":"r.execution_attempt=1",
        "target":"r.target_agent_id='xwing'",
        "machine":"r.machine_authorized=true",
        "machine-gen":"r.machine_authorization_generation=17",
        "machine-approval":"r.machine_authorization_approval_id='approval'",
        "token-epoch":"r.machine_token_epoch=2",
        "projection":"r.projection_generation=3",
        "hermes":"r.hermes_claim_scope=true",
        "auto-dispatch":"r.auto_dispatch=true",
    }
    with driver.session() as s:
        if case in mutations:
            s.run("MATCH (r:Task {id:$id}) SET "+mutations[case],
                  {"id":REVIEW_ID}).consume()
        if case=="approved-link":
            s.run("""
                MATCH (r:Task {id:$id})
                CREATE (r)-[:APPROVED_AS]->(:Task {id:'existing-child'})
            """,{"id":REVIEW_ID}).consume()
        if case=="candidate-id":
            s.run("CREATE (:Task {id:$id})",
                  {"id":candidate_id()}).consume()
        if case=="candidate-origin":
            s.run("CREATE (:Task {id:'existing-child', approved_from_review_task:$id})",
                  {"id":REVIEW_ID}).consume()
        if case=="outbox-id":
            s.run("CREATE (:ReviewApprovalOutbox {id:$id})",
                  {"id":outbox_id()}).consume()
    before=snapshot(driver)
    with pytest.raises(research.ReviewAtomicApprovalDenied):
        approve(driver,revision=REVISION-1 if case=="stale-revision" else REVISION)
    assert snapshot(driver)==before,case


def test_racing_approvals_have_exactly_one_winner(driver):
    seed(driver)
    gate=Barrier(2)
    def attempt(actor):
        gate.wait(timeout=12)
        try:
            return approve(driver,actor=actor)
        except research.ReviewAtomicApprovalDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        f=pool.submit(attempt,"reviewer-A")
        g=pool.submit(attempt,"reviewer-B")
        result=[f.result(timeout=20),g.result(timeout=20)]
    assert sum(x is not None for x in result)==1
    reviews,children,outboxes,links,serials,dispatch=snapshot(driver)
    assert len(reviews)==len(children)==len(outboxes)==1
    assert reviews[0]["reviewed_by"] in {"reviewer-A","reviewer-B"}
    assert links==1 and serials==[1] and dispatch==0


def test_lock_held_revocation_refuses_after_wait(driver):
    seed(driver)
    with driver.session() as s:
        tx=s.begin_transaction()
        try:
            tx.run("""
                MATCH (w:WorkSupplyState {name:'canonical'})
                SET w.synthetic_owner_hold=1
            """).consume()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(approve,driver)
                try:
                    with pytest.raises(TimeoutError):
                        future.result(timeout=0.3)
                finally:
                    tx.run("""
                        MATCH (r:Task {id:$id})
                        SET r.machine_authorized=true,
                            r.machine_authorization_generation=17
                    """,{"id":REVIEW_ID}).consume()
                    tx.commit()
                with pytest.raises(research.ReviewAtomicApprovalDenied):
                    future.result(timeout=20)
        finally:
            if not tx.closed():
                tx.rollback()
    reviews,children,outboxes,links,serials,dispatch=snapshot(driver)
    assert reviews[0]["status"]=="REVIEW"
    assert reviews[0]["machine_authorized"] is True
    assert len(children)==len(outboxes)==0
    assert links==0 and serials==[0] and dispatch==0


def test_fault_after_all_graph_creates_rolls_back_entire_transaction(driver,monkeypatch):
    seed(driver)
    original=research._tx_approve
    def injected_failure(tx,params):
        original(tx,params)
        raise RuntimeError("synthetic_after_graph_commit_stage")
    monkeypatch.setattr(research,"_tx_approve",injected_failure)
    before=snapshot(driver)
    with pytest.raises(RuntimeError,match="synthetic_after_graph"):
        approve(driver)
    assert snapshot(driver)==before


def test_auto_dispatch_request_refused_before_graph_open(driver):
    seed(driver)
    before=snapshot(driver)
    with pytest.raises(research.ReviewAtomicApprovalDenied,match="dispatching"):
        approve(driver,dispatch=True)
    assert snapshot(driver)==before
