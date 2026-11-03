"""Disposable Neo4j only — never production; synthetic Task/Intent records."""
from __future__ import annotations

import time
import pytest
from neo4j import GraphDatabase

from assistx.legacy_intent_cancel import (
    LegacyIntentCancelBlocked,
    cancel_legacy_intent_tasks,
)


@pytest.fixture(scope="module")
def driver(neo4j_container):
    driver = GraphDatabase.driver(
        neo4j_container["uri"],
        auth=(neo4j_container["user"], neo4j_container["password"]),
    )
    yield driver
    driver.close()


class Neo:
    def __init__(self, driver):
        self.driver = driver

    def _session(self):
        return self.driver.session()


def seed(driver, *, with_lock=True):
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        if with_lock:
            s.run(
                "CREATE (:WorkSupplyState {name:'canonical', legacy_intent_cancel_serial:0})"
            ).consume()
        s.run("""
            CREATE (i:Intent {id:'synthetic-intent'})
            CREATE (ordinary:Task {id:'legacy-ready',status:'READY'})
            CREATE (running:Task {id:'legacy-running',status:'RUNNING'})
            CREATE (scoped:Task {id:'machine-ready',status:'READY',
                machine_authorized:true,machine_authorization_generation:17})
            CREATE (claimed:Task {id:'machine-claimed',status:'CLAIMED',
                machine_authorized:true,machine_token_epoch:2})
            CREATE (hermes:Task {id:'hermes-claimed',status:'CLAIMED',
                hermes_claim_scope:true})
            CREATE (i)-[:CREATED_TASK]->(ordinary)
            CREATE (i)-[:CREATED_TASK]->(running)
            CREATE (i)-[:CREATED_TASK]->(scoped)
            CREATE (i)-[:CREATED_TASK]->(claimed)
            CREATE (i)-[:CREATED_TASK]->(hermes)
        """).consume()


def snapshot(driver):
    with driver.session() as s:
        rows = s.run("MATCH (t:Task) RETURN t.id AS id,t.status AS status ORDER BY id")
        return {r["id"]:r["status"] for r in rows}


def test_protected_machine_and_hermes_task_not_cancelled(driver):
    seed(driver)
    result = cancel_legacy_intent_tasks(
        Neo(driver), intent_id="synthetic-intent", reason="synthetic-reason",
    )
    assert result == 2
    got = snapshot(driver)
    assert got == {
        "legacy-ready": "CANCELLED",
        "legacy-running": "CANCELLED",
        "machine-ready": "READY",
        "machine-claimed": "CLAIMED",
        "hermes-claimed": "CLAIMED",
    }
    with driver.session() as s:
        row = s.run(
            "MATCH (w:WorkSupplyState {name:'canonical'}) "
            "RETURN w.legacy_intent_cancel_serial AS serial"
        ).single()
        assert row["serial"] == 1


def test_missing_canonical_lock_rolls_back_all_cancellation(driver):
    seed(driver, with_lock=False)
    with pytest.raises(LegacyIntentCancelBlocked, match="lock_missing"):
        cancel_legacy_intent_tasks(
            Neo(driver), intent_id="synthetic-intent", reason="synthetic",
        )
    got = snapshot(driver)
    assert got["legacy-ready"] == "READY"
    assert got["machine-claimed"] == "CLAIMED"
