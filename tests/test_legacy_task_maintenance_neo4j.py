"""Real disposable Neo4j tests; synthetic Tasks only; no production connection."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest
from neo4j import GraphDatabase

from assistx.legacy_task_maintenance import (
    MaintenanceTaskWriteBlocked,
    reap_stale_legacy_tasks,
    retain_terminal_legacy_tasks,
)


@pytest.fixture(scope="module")
def driver(neo4j_container):
    drv = GraphDatabase.driver(
        neo4j_container["uri"],
        auth=(neo4j_container["user"], neo4j_container["password"]),
    )
    yield drv
    drv.close()


class Neo:
    def __init__(self, driver):
        self.driver = driver

    def _session(self):
        return self.driver.session()


def seed(driver, *, coordination=1):
    with driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n").consume()
        for _ in range(coordination):
            s.run(
                "CREATE (:WorkSupplyState {name:'canonical',legacy_maintenance_serial:0})"
            ).consume()
        s.run("""
        CREATE (:Task {id:'legacy-stale',status:'CLAIMED',
                       claimed_by:'ordinary-worker',lease_expires_at_ts:0})
        CREATE (:Task {id:'legacy-fresh',status:'RUNNING',
                       claimed_by:'ordinary-worker',
                       lease_expires_at_ts:9999999999999})
        CREATE (:Task {id:'machine-stale',status:'CLAIMED',
                       machine_authorized:true,
                       machine_authorization_generation:17,
                       machine_token_epoch:2,target_agent_id:'xwing',
                       claimed_by:'xwing',lease_expires_at_ts:0})
        CREATE (:Task {id:'partial-machine-stale',status:'CLAIMED',
                       machine_authorization_approval_id:'synthetic-approval',
                       claimed_by:'ordinary-worker',lease_expires_at_ts:0})
        CREATE (:Task {id:'hermes-stale',status:'CLAIMED',
                       hermes_claim_scope:true,claimed_by:'hermes-local',
                       lease_expires_at_ts:0})
        CREATE (:Task {id:'targeted-stale',status:'CLAIMED',
                       target_agent_id:'xwing',claimed_by:'ordinary-worker',
                       lease_expires_at_ts:0})
        CREATE (:Task {id:'legacy-old',status:'DONE',updated_at_ts:0})
        CREATE (:Task {id:'machine-old',status:'DONE',updated_at_ts:0,
                       machine_authorization_generation:17,
                       machine_authorization_approval_id:'synthetic-approval'})
        CREATE (:Task {id:'hermes-old',status:'FAILED',updated_at_ts:0,
                       hermes_claim_scope:true})
        CREATE (:Task {id:'targeted-old',status:'CANCELLED',updated_at_ts:0,
                       target_agent_id:'xwing'})
        CREATE (:Task {id:'recent-terminal',status:'DONE',
                       updated_at_ts:9999999999999})
        """).consume()


def snapshot(driver):
    with driver.session() as s:
        return {row["id"]: row["status"] for row in s.run(
            "MATCH (t:Task) RETURN t.id AS id,t.status AS status"
        )}


def test_stale_reaper_retains_protected_and_fresh_tasks(driver):
    seed(driver)
    assert reap_stale_legacy_tasks(Neo(driver), lease_seconds=600) == 1
    state = snapshot(driver)
    assert state["legacy-stale"] == "READY"
    assert state["legacy-fresh"] == "RUNNING"
    assert state["machine-stale"] == "CLAIMED"
    assert state["partial-machine-stale"] == "CLAIMED"
    assert state["hermes-stale"] == "CLAIMED"
    assert state["targeted-stale"] == "CLAIMED"


def test_retention_preserves_protected_and_recent_task_history(driver):
    seed(driver)
    assert retain_terminal_legacy_tasks(Neo(driver), retention_days=30) == 1
    state = snapshot(driver)
    assert "legacy-old" not in state
    assert state["machine-old"] == "DONE"
    assert state["hermes-old"] == "FAILED"
    assert state["targeted-old"] == "CANCELLED"
    assert state["recent-terminal"] == "DONE"


@pytest.mark.parametrize("coordination", [0, 2])
def test_missing_or_duplicate_lock_rolls_back_reap_and_retention(driver, coordination):
    seed(driver, coordination=coordination)
    for operation, kwargs in [
        (reap_stale_legacy_tasks, {"lease_seconds": 600}),
        (retain_terminal_legacy_tasks, {"retention_days": 30}),
    ]:
        with pytest.raises(MaintenanceTaskWriteBlocked, match="not_unique"):
            operation(Neo(driver), **kwargs)
    state = snapshot(driver)
    assert state["legacy-stale"] == "CLAIMED"
    assert state["legacy-old"] == "DONE"
    with driver.session() as s:
        counters = [r["serial"] for r in s.run(
            "MATCH (w:WorkSupplyState {name:'canonical'}) "
            "RETURN w.legacy_maintenance_serial AS serial"
        )]
        assert counters == [0] * coordination


def test_maintenance_waits_for_same_canonical_lock(driver):
    """Prove the maintenance writer actually conflicts with another lock owner."""
    seed(driver)
    with driver.session() as s:
        tx = s.begin_transaction()
        try:
            tx.run("""
                MATCH (w:WorkSupplyState {name:'canonical'})
                SET w.synthetic_coordinator_hold=1
                RETURN w.name AS name
            """).consume()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    reap_stale_legacy_tasks, Neo(driver), lease_seconds=600
                )
                try:
                    with pytest.raises(TimeoutError):
                        pending.result(timeout=0.3)
                finally:
                    tx.commit()
                assert pending.result(timeout=15) == 1
        finally:
            if not tx.closed():
                tx.rollback()
    assert snapshot(driver)["legacy-stale"] == "READY"
