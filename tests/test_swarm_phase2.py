import os

import pytest
from fastapi.testclient import TestClient

from assistx.api import app
from assistx.swarm_core import (
    EventConflictError,
    action_requires_approval,
    fail_task,
    list_capabilities,
    list_swarm_nodes,
    record_event,
    release_expired_task_leases,
    set_task_lease,
    upsert_swarm_node,
)


def _auth():
    return (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))


def _base_event(**overrides):
    event = {
        "event_id": "event-1",
        "event_type": "voice.quick_input.created",
        "source_repo": "Sophia",
        "source_service": "voice-agent",
        "node_id": "x1-370",
        "occurred_at": "2026-05-26T18:00:00-04:00",
        "idempotency_key": "capture-1:utterance-1",
        "schema_version": "1.0",
        "subject": {"kind": "utterance", "id": "utterance-1"},
        "payload": {
            "text": "Create a draft note about the swarm plan.",
            "auth_state": "authenticated_scott",
            "action": "create_draft_task",
            "risk_level": "low",
            "speaker_identity": "scott",
            "speaker_confidence": 0.91,
        },
        "artifact_refs": [],
        "privacy": {"pii": True, "privacy_class": "private", "retention_class": "keep"},
    }
    event.update(overrides)
    return event


def test_event_replay_idempotency_and_conflict(seeded_neo4j):
    event = _base_event()
    first = record_event(seeded_neo4j, event)
    assert first["accepted"] is True
    assert first["deduped"] is False
    assert first["graph_reconciled"] is True

    second = record_event(seeded_neo4j, event)
    assert second["accepted"] is True
    assert second["deduped"] is True

    changed = _base_event(payload={**event["payload"], "text": "Conflicting text"})
    with pytest.raises(EventConflictError):
        record_event(seeded_neo4j, changed)


def test_unknown_speaker_requires_approval_and_scott_low_risk_auto_approval(seeded_neo4j):
    unknown = _base_event(
        event_id="event-unknown",
        idempotency_key="capture-2:utterance-1",
        payload={
            "text": "Create a task as an unknown user.",
            "auth_state": "unknown_speaker",
            "action": "create_draft_task",
            "risk_level": "low",
        },
    )
    record_event(seeded_neo4j, unknown)
    task = seeded_neo4j.get_task("task:event-unknown")
    assert task["status"] == "REVIEW"
    assert task["approval_required"] is True

    scott = _base_event(
        event_id="event-scott",
        idempotency_key="capture-3:utterance-1",
        payload={
            "text": "Create a low risk draft.",
            "auth_state": "authenticated_scott",
            "action": "create_draft_task",
            "risk_level": "low",
        },
    )
    record_event(seeded_neo4j, scott)
    task2 = seeded_neo4j.get_task("task:event-scott")
    assert task2["status"] == "READY"
    assert task2["approval_required"] is False
    assert action_requires_approval("authenticated_scott", "create_draft_task", "low") is False
    assert action_requires_approval("unknown_speaker", "create_draft_task", "low") is True
    assert action_requires_approval("authenticated_scott", "delete_files", "high") is True


def test_swarm_node_registry_and_capability_listing(seeded_neo4j):
    node = upsert_swarm_node(
        seeded_neo4j,
        {
            "node_id": "demo-1",
            "hostname": "demo-1",
            "status": "online",
            "roles": ["fast_delegation_agent", "model_endpoint"],
            "capabilities": [
                {"capability_id": "demo-1.llm.fast", "kind": "llm", "name": "Fast local generation"}
            ],
        },
    )
    assert node["node_id"] == "demo-1"
    nodes = list_swarm_nodes(seeded_neo4j)
    assert any(n["node_id"] == "demo-1" for n in nodes)
    caps = list_capabilities(seeded_neo4j)
    assert any(c["capability_id"] == "demo-1.llm.fast" for c in caps)


def test_task_claim_heartbeat_complete_fail_and_lease_release(seeded_neo4j):
    task = seeded_neo4j.get_ready_tasks()[0]
    claimed = seeded_neo4j.claim_task(task["id"], agent_id="agent-1", capabilities=[])
    assert claimed["claimed"] is True
    claimed_task = seeded_neo4j.get_task(task["id"])
    assert claimed_task["status"] == "CLAIMED"
    assert claimed_task.get("lease_expires_at_ts") is not None

    heartbeated = seeded_neo4j.heartbeat_task(task["id"], agent_id="agent-1", status="RUNNING")
    assert heartbeated["status"] == "RUNNING"
    assert heartbeated.get("lease_expires_at_ts") is not None

    set_task_lease(seeded_neo4j, task["id"], lease_seconds=1)
    with seeded_neo4j._session() as s:
        s.run("MATCH (t:Task {id:$id}) SET t.lease_expires_at_ts=0", {"id": task["id"]}).consume()
    released = release_expired_task_leases(seeded_neo4j, now_ms=1)
    assert released == 1
    assert seeded_neo4j.get_task(task["id"])["status"] == "READY"

    complete_id = seeded_neo4j.upsert_ticket(
        title="Complete a swarm task",
        ticket_type="task",
        status="READY",
        kind="phase2_test_complete",
    )
    claimed_complete = seeded_neo4j.claim_task(complete_id, agent_id="agent-1", capabilities=[])
    assert claimed_complete["claimed"] is True
    completed = seeded_neo4j.complete_task(
        complete_id,
        agent_id="agent-1",
        status="DONE",
        summary="completed by phase2 test",
        result={"ok": True},
    )
    assert completed["status"] == "DONE"
    assert completed["run_id"]

    failed = fail_task(seeded_neo4j, task["id"], agent_id="agent-1", error_summary="temporary model outage", retryable=True)
    assert failed["status"] == "READY"
    assert failed["failure_count"] >= 1

    terminal = fail_task(seeded_neo4j, task["id"], agent_id="agent-1", error_summary="non retryable", retryable=False)
    assert terminal["status"] == "FAILED"


def test_swarm_routes_registered(monkeypatch, seeded_neo4j):
    monkeypatch.setattr("assistx.swarm_routes._neo", lambda: seeded_neo4j)
    monkeypatch.setattr(seeded_neo4j, "close", lambda: None)
    client = TestClient(app)
    response = client.post("/api/events", json=_base_event(event_id="route-event", idempotency_key="route-key"), auth=_auth())
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True

    nodes = client.post(
        "/api/swarm/nodes/register",
        json={
            "node_id": "x1-370",
            "hostname": "x1-370",
            "status": "online",
            "roles": ["primary_knowledge_host"],
            "capabilities": [{"capability_id": "x1-370.assistx.control", "kind": "orchestration"}],
        },
        auth=_auth(),
    )
    assert nodes.status_code == 200, nodes.text

    listed = client.get("/api/swarm/nodes", auth=_auth())
    assert listed.status_code == 200, listed.text
    assert any(item["node_id"] == "x1-370" for item in listed.json()["items"])
