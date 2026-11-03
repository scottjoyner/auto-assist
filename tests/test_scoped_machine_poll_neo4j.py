"""Research-only graph integration for machine Task hints.

Uses the existing disposable, isolated Docker Neo4j test fixture and exclusively
synthetic data. The candidate module opens READ_ACCESS sessions for all reads.
No production graph connections, Tasks, users, machine tokens or agents.
"""
from __future__ import annotations

import time

import pytest
from neo4j import READ_ACCESS, GraphDatabase

from assistx import scoped_machine_poll as poll

ARTIFACT = "a" * 64
GEN = 17
EPOCH = 3


class ReadOnlyNeo:
    def __init__(self, driver):
        self.driver = driver

    def _session(self):
        return self.driver.session(default_access_mode=READ_ACCESS)

    def close(self):
        pass


@pytest.fixture(scope="module")
def isolated_driver(neo4j_container):
    driver = GraphDatabase.driver(
        neo4j_container["uri"],
        auth=(neo4j_container["user"], neo4j_container["password"]),
    )
    yield driver
    driver.close()


def seed(driver, *, alteration=None):
    now = int(time.time() * 1000)
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
        session.run(
            """
            CREATE (p:FleetProjectionState {
              name:'canonical',status:'approved',generation:$generation,
              approved_by:'synthetic-owner',approval_id:'synthetic-approval',
              manifest_checksum:'synthetic-manifest',expires_at_ts:$expiry})
            CREATE (s:WorkSupplyState {
              name:'canonical',projection_generation:$generation,
              allow_background:true,total_slots:2,free_slots:2,
              generated_at_ts:$now})
            CREATE (auth:MachinePrincipalAdmission {
              node_id:'xwing',status:'approved',projection_generation:$generation,
              token_epoch:$epoch,artifact_fingerprint:$artifact,
              approved_by:'synthetic-owner',approval_id:'synthetic-node-approval',
              expires_at_ts:$expiry})
            CREATE (r:RuntimeInstance {
              node_id:'xwing',runtime_instance_id:'synthetic-runtime',
              status:'online',admitted:true,expires_at_ts:$expiry})
            CREATE (m:LoadedModelInstance {
              model_instance_id:'synthetic-chat',admitted:true,
              machine_chat_eligible:true,
              capabilities_json:'["chat","llm"]',
              artifact_fingerprint:$artifact,expires_at_ts:$expiry})
            CREATE (r)-[:SERVES]->(m)
            CREATE (a:AccessPath {
              runtime_instance_id:'synthetic-runtime',approved:true,
              approved_by:'synthetic-owner',approval_id:'synthetic-access',
              expires_at_ts:$expiry})
            CREATE (c:CapacityObservation {
              runtime_instance_id:'synthetic-runtime',approved:true,
              approved_by:'synthetic-owner',approval_id:'synthetic-capacity',
              parallel_slots:2,expires_at_ts:$expiry})
            CREATE (t:Task {
              id:'synthetic-task',status:'READY',target_agent_id:'xwing',
              created_at_ts:$now,required_capabilities:['llm'],
              requires_approval:false,paperclip_dispatched:false,
              machine_authorized:true,
              machine_authorization_generation:$generation,
              machine_authorization_approval_id:'synthetic-node-approval',
              owner_id:'synthetic-owner'})
            """,
            {"generation": GEN, "epoch": EPOCH, "artifact": ARTIFACT,
             "now": now, "expiry": now + 120_000},
        ).consume()
        updates = {
            "missing_machine_authority":
                "MATCH (t:Task) SET t.machine_authorized=false",
            "wrong_task_generation":
                "MATCH (t:Task) SET t.machine_authorization_generation=16",
            "different_machine_approval":
                "MATCH (t:Task) SET t.machine_authorization_approval_id='other'",
            "missing_owner":
                "MATCH (t:Task) REMOVE t.owner_id",
            "claimed_task":
                "MATCH (t:Task) SET t.claim_id='old-claim'",
            "replayed_execution_attempt":
                "MATCH (t:Task) SET t.execution_attempt=1",
            "embedding_only":
                "MATCH (m:LoadedModelInstance) SET m.machine_chat_eligible=false",
            "revoked_principal":
                "MATCH (auth:MachinePrincipalAdmission) SET auth.status='revoked'",
            "wrong_token_epoch":
                "MATCH (auth:MachinePrincipalAdmission) SET auth.token_epoch=2",
            "duplicate_principal":
                """MATCH (auth:MachinePrincipalAdmission)
                   CREATE (copy:MachinePrincipalAdmission)
                   SET copy=properties(auth)""",
        }
        if alteration:
            session.run(updates[alteration]).consume()


@pytest.mark.parametrize("mode", [
    None, "missing_machine_authority", "wrong_task_generation",
    "different_machine_approval", "missing_owner", "claimed_task",
    "replayed_execution_attempt", "embedding_only",
    "revoked_principal", "wrong_token_epoch", "duplicate_principal",
])
def test_readonly_machine_hints_require_exact_scoped_graph_admission(
    isolated_driver, monkeypatch, mode,
):
    """Positive and denied queries must never mutate synthetic graph state."""
    seed(isolated_driver, alteration=mode)
    monkeypatch.setattr(
        poll,
        "_projection_state",
        lambda factory, now: {"generation": GEN, "revision": "synthetic"},
    )
    with isolated_driver.session() as session:
        before = session.run("MATCH (t:Task) RETURN properties(t) AS p").single()["p"]
    output = None
    try:
        output = poll.candidate_tasks(
            "xwing", neo_factory=lambda: ReadOnlyNeo(isolated_driver),
            expected_generation=GEN, expected_artifact=ARTIFACT,
            expected_token_epoch=EPOCH, limit=10,
        )
    except poll.HTTPException as exc:
        assert mode in ("revoked_principal", "wrong_token_epoch", "duplicate_principal")
        assert exc.status_code == 503
    else:
        assert output["authorizes_claim"] is False
        assert output["authorizes_dispatch"] is False
        assert output["items"] == (
            [{"id": "synthetic-task", "requires_capability": "llm"}]
            if mode is None else []
        )
    with isolated_driver.session() as session:
        after = session.run("MATCH (t:Task) RETURN properties(t) AS p").single()["p"]
    assert before == after
