"""Physical read-only audit tests. Graph mutations seed DISPOSABLE Neo4j only."""
from __future__ import annotations

import json

import pytest
from neo4j import GraphDatabase

from assistx.review_orphan_audit import AuditIncomplete, audit_review_provenance
from assistx.review_atomic_approval import propose_held_review_approval


@pytest.fixture(scope="module")
def driver(neo4j_container):
    drv=GraphDatabase.driver(neo4j_container["uri"],auth=(
        neo4j_container["user"],neo4j_container["password"]))
    yield drv
    drv.close()


class Neo:
    def __init__(self,driver):self.driver=driver
    def _session(self):return self.driver.session()


def clear(driver):
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()


def review(driver, rid="synthetic-review", status="DONE", decision="approved"):
    with driver.session() as s:
        s.run("""
            CREATE (:Task {id:$id,kind:'intent_review',
                   status:$status,review_decision:$decision,
                   updated_at_ts:1700000123000})
        """,{"id":rid,"status":status,"decision":decision}).consume()


def child(driver, cid="synthetic-child", rid="synthetic-review",
          status="READY", direct=False, malformed=False):
    p=("not json" if malformed else json.dumps(
        {} if direct else {"approved_from_review_task":rid}
    ))
    with driver.session() as s:
        s.run("""
            CREATE (:Task {id:$cid, kind:'approved_intent',
                status:$status, payload_json:$payload,
                approved_from_review_task:$direct_ref})
        """,{"cid":cid,"status":status,"payload":p,
             "direct_ref":rid if direct else None}).consume()


def link(driver, rid="synthetic-review", cid="synthetic-child"):
    with driver.session() as s:
        s.run("""
            MATCH (r:Task {id:$rid}), (c:Task {id:$cid})
            CREATE (r)-[:APPROVED_AS]->(c)
        """,{"rid":rid,"cid":cid}).consume()


def audit(driver,limit=150):
    return audit_review_provenance(driver,limit=limit)


def test_empty_sample_is_hold_not_green(driver):
    clear(driver)
    result=audit(driver)
    assert result["decision"]=="HOLD"
    assert result["read_only"] is True
    assert result["counts"]=={"reviews":0,"children":0,"outboxes":0}
    assert result["coverage"]=="bounded_current_graph_only_not_external_custody"


def test_legacy_child_linked_approved_not_orphan(driver):
    clear(driver)
    review(driver)
    child(driver)
    link(driver)
    result=audit(driver)
    assert result["approved_review_missing_unique_child_link"]==0
    assert result["child_link_findings"]=={"linked_with_finalized_review":1}
    assert result["child_pointer_findings"]=={}
    assert result["review_statuses"]=={"DONE":1}


def test_legacy_child_without_review_is_flagged_but_never_modified(driver):
    clear(driver)
    child(driver)
    first=audit(driver)
    second=audit(driver)
    assert first==second
    assert first["child_link_findings"]=={"missing_or_ambiguous_source_review":1}
    with driver.session() as s:
        assert s.run("MATCH (t:Task) RETURN count(t) AS n").single()["n"]==1


def test_approved_review_without_child_link_is_flagged(driver):
    clear(driver)
    review(driver)
    assert audit(driver)["approved_review_missing_unique_child_link"]==1


def test_child_without_relationship_to_existing_approved_review(driver):
    clear(driver)
    review(driver)
    child(driver)
    result=audit(driver)
    assert result["approved_review_missing_unique_child_link"]==1
    assert result["child_link_findings"]=={"missing_or_ambiguous_approval_link":1}


def test_child_linked_to_review_not_approved(driver):
    clear(driver)
    review(driver,status="REVIEW",decision=None)
    child(driver)
    link(driver)
    assert audit(driver)["child_link_findings"]=={"linked_review_not_approved":1}


def test_two_children_on_same_approved_review_detects_nonunique_approval(driver):
    clear(driver)
    review(driver)
    child(driver,cid="child-A")
    child(driver,cid="child-B")
    link(driver,cid="child-A")
    link(driver,cid="child-B")
    assert audit(driver)["approved_review_missing_unique_child_link"]==1


def test_malformed_or_missing_source_pointer_not_conflated_with_orphan(driver):
    clear(driver)
    child(driver,cid="bad",malformed=True)
    child(driver,cid="no-pointer",rid=None,direct=False)
    result=audit(driver)
    assert result["child_pointer_findings"]=={
        "malformed_payload":1,"no_source_pointer":1}
    assert result["child_link_findings"]=={}


def test_held_outbox_prototype_is_classified_without_dispatch(driver):
    clear(driver)
    with driver.session() as s:
        s.run("CREATE (:WorkSupplyState {name:'canonical'})").consume()
        s.run("""
            CREATE (:Task {id:'synthetic-review',status:'REVIEW',
              kind:'intent_review',title:'Synthetic review',
              payload_json:'{"source_text":"Held candidate"}',
              updated_at_ts:1700000123000,execution_attempt:0})
        """).consume()
    created=propose_held_review_approval(
        Neo(driver),review_id="synthetic-review",
        actor="synthetic-operator",expected_revision=1700000123000,
    )
    assert created["outbox_status"]=="HELD_UNTIL_ADMISSION"
    report=audit(driver)
    assert report["counts"]=={"reviews":1,"children":1,"outboxes":1}
    assert report["child_link_findings"]=={"linked_with_finalized_review":1}
    assert report["outbox_findings"]=={}
    assert report["decision"]=="HOLD"
    with driver.session() as s:
        assert s.run("MATCH (d:Dispatch) RETURN count(d) AS n").single()["n"]==0


def test_outbox_with_missing_links_or_elevated_flags_is_flagged(driver):
    clear(driver)
    with driver.session() as s:
        s.run("""
            CREATE (:ReviewApprovalOutbox {id:'synthetic-outbox',
              review_task_id:'missing-review',
              candidate_task_id:'missing-child',status:'PENDING_DISPATCH',
              verified_operator:true,signed_admission:true})
        """).consume()
    report=audit(driver)
    assert report["outbox_findings"]=={
        "incomplete_outbox_links":1,
        "unexpected_or_elevated_outbox":1,
    }


def test_sample_limit_explicitly_reports_incomplete_and_holds(driver):
    clear(driver)
    review(driver,rid="r-1")
    review(driver,rid="r-2")
    report=audit(driver,limit=1)
    assert report["truncated"]["reviews"] is True
    assert report["counts"]["reviews"]==1
    assert report["decision"]=="HOLD"


def test_duplicate_task_id_never_approves_a_clean_sample(driver):
    clear(driver)
    review(driver,rid="same")
    review(driver,rid="same")
    with pytest.raises(AuditIncomplete,match="duplicate_id"):
        audit(driver)
