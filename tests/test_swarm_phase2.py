import json
import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from assistx.api import app
from assistx.swarm_core import (
    EventConflictError,
    action_requires_approval,
    fail_task,
    list_capabilities,
    list_model_endpoints,
    list_swarm_nodes,
    record_event,
    release_expired_task_leases,
    set_task_lease,
    upsert_model_endpoint,
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


def test_event_metadata_is_persisted_for_router_and_assign_consumers(seeded_neo4j):
    event = _base_event(event_id="event-metadata", idempotency_key="metadata-key")
    record_event(seeded_neo4j, event)

    with seeded_neo4j._session() as s:
        rec = s.run(
            "MATCH (e:EventEnvelope {event_id:$event_id}) RETURN e.metadata_json AS metadata_json, e.payload_json AS payload_json",
            {"event_id": event["event_id"]},
        ).single()

    metadata = json.loads(rec["metadata_json"])
    assert metadata["request"]["request_id"] == event["event_id"]
    assert metadata["request"]["event_type"] == event["event_type"]
    assert metadata["request"]["subject_id"] == event["subject"]["id"]
    assert metadata["context"]["contract"] == "assistx.event_metadata.v1"
    assert "text" not in metadata["request"]


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


def test_model_endpoint_refresh_removes_stale_served_relationships(seeded_neo4j):
    endpoint = {
        "model_endpoint_id": "test-mac.lmstudio",
        "node_id": "test-mac",
        "base_url": "http://test-mac:1234/v1",
        "models": [
            {"model_id": "test-mac.old", "served_name": "old"},
            {"model_id": "test-mac.keep", "served_name": "keep"},
        ],
    }
    upsert_model_endpoint(seeded_neo4j, endpoint)
    upsert_model_endpoint(
        seeded_neo4j,
        {
            **endpoint,
            "models": [
                {"model_id": "test-mac.keep", "served_name": "keep"},
                {"model_id": "test-mac.new", "served_name": "new"},
            ],
        },
    )
    current = next(
        item for item in list_model_endpoints(seeded_neo4j)
        if item["model_endpoint_id"] == "test-mac.lmstudio"
    )
    assert {model["model_id"] for model in current["models"]} == {"test-mac.keep", "test-mac.new"}


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


def test_direct_worker_cannot_claim_paperclip_dispatched_task(seeded_neo4j):
    task_id = seeded_neo4j.upsert_ticket(
        title="Paperclip owns this execution",
        ticket_type="task",
        status="READY",
        kind="paperclip_reserved_test",
    )
    seeded_neo4j.create_dispatch(
        task_id=task_id,
        target={"paperclip_issue_id": "paperclip-issue-1"},
    )

    ready_ids = {task["id"] for task in seeded_neo4j.list_agent_tasks(status="READY")}
    assert task_id not in ready_ids

    claimed = seeded_neo4j.claim_task(task_id, agent_id="legacy-worker", capabilities=[])
    assert claimed == {"claimed": False, "reason": "paperclip_dispatched"}


def test_dispatch_creation_reuses_dispatch_created_by_early_paperclip_event(seeded_neo4j):
    issue_id = f"paperclip-race-issue-{uuid.uuid4().hex}"
    seeded_neo4j.ingest_paperclip_event(
        event_type="run_started",
        paperclip_issue_id=issue_id,
        paperclip_agent_id="paperclip-agent-1",
        paperclip_run_id="paperclip-run-1",
        event_id="paperclip-race-event-1",
        payload={},
    )
    task_id = seeded_neo4j.upsert_ticket(
        title="Paperclip starts before local dispatch link",
        ticket_type="task",
        status="READY",
        kind="paperclip_race_test",
    )
    dispatch_id = seeded_neo4j.create_dispatch(
        task_id=task_id,
        target={"paperclip_issue_id": issue_id, "paperclip_agent_id": "paperclip-agent-1"},
    )

    with seeded_neo4j._session() as s:
        result = s.run(
            "MATCH (d:Dispatch {paperclip_issue_id:$issue_id}) "
            "OPTIONAL MATCH (t:Task {id:$task_id})-[:DISPATCHED_AS]->(d) "
            "RETURN count(d) AS dispatch_count, count(t) AS linked_task_count, "
            "collect(d.id) AS dispatch_ids, collect(d.status) AS statuses",
            {"issue_id": issue_id, "task_id": task_id},
        ).single()
    assert result["dispatch_count"] == 1
    assert result["linked_task_count"] == 1
    assert result["dispatch_ids"] == [dispatch_id]
    assert result["statuses"] == ["RUNNING"]


def test_swarm_routes_registered(monkeypatch, seeded_neo4j):
    monkeypatch.setattr("assistx.swarm_routes._neo", lambda: seeded_neo4j)
    monkeypatch.setattr(seeded_neo4j, "close", lambda: None)
    client = TestClient(app)
    response = client.post("/api/events", json={**_base_event(event_id="route-event", idempotency_key="route-key"), "unexpected": "ignored"}, auth=_auth())
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
            "unexpected": "ignored",
        },
        auth=_auth(),
    )
    assert nodes.status_code == 200, nodes.text

    listed = client.get("/api/swarm/nodes", auth=_auth())
    assert listed.status_code == 200, listed.text
    assert any(item["node_id"] == "x1-370" for item in listed.json()["items"])


def test_model_endpoint_routes_and_draft_generation(monkeypatch, seeded_neo4j):
    monkeypatch.setattr("assistx.swarm_routes._neo", lambda: seeded_neo4j)
    monkeypatch.setattr(seeded_neo4j, "close", lambda: None)
    monkeypatch.setattr(
        "assistx.swarm_routes.probe_model_endpoint",
        lambda neo, endpoint: {
            "model_endpoint_id": endpoint["model_endpoint_id"],
            "status": "online",
            "models_count": 1,
        },
    )
    monkeypatch.setattr(
        "assistx.swarm_routes.generate_draft",
        lambda prompt, max_tokens: {
            "text": f"Draft: {prompt}",
            "model": "qwen3.5-0.8b",
            "source": "configured_draft_endpoint",
        },
    )
    client = TestClient(app)
    registered = client.post(
        "/api/swarm/model-endpoints/register",
        json={
            "model_endpoint_id": "scotts-macbook-air.lmstudio",
            "node_id": "scotts-macbook-air",
            "base_url": "http://100.85.64.117:1234/v1",
            "network_preference": "tailscale",
            "purpose": "low-risk drafting",
        },
        auth=_auth(),
    )
    assert registered.status_code == 200, registered.text

    listed = client.get("/api/swarm/model-endpoints", auth=_auth())
    assert listed.status_code == 200, listed.text
    assert any(
        item["model_endpoint_id"] == "scotts-macbook-air.lmstudio"
        for item in listed.json()["items"]
    )

    probed = client.post("/api/swarm/model-endpoints/scotts-macbook-air.lmstudio/probe", auth=_auth())
    assert probed.status_code == 200, probed.text
    assert probed.json()["status"] == "online"

    draft = client.post("/api/drafts/generate", json={"prompt": "Brief status update."}, auth=_auth())
    assert draft.status_code == 200, draft.text
    assert draft.json()["model"] == "qwen3.5-0.8b"


def test_swarm_auth_401(monkeypatch, seeded_neo4j):
    monkeypatch.setattr("assistx.swarm_routes._neo", lambda: seeded_neo4j)
    monkeypatch.setattr(seeded_neo4j, "close", lambda: None)
    client = TestClient(app)
    resp = client.post("/api/events", json=_base_event(event_id="auth-event", idempotency_key="auth-key"))
    assert resp.status_code == 401, resp.text
    resp2 = client.post("/api/swarm/nodes/register", json={"node_id": "unauth-node"})
    assert resp2.status_code == 401, resp2.text
    resp3 = client.get("/api/swarm/nodes")
    assert resp3.status_code == 401, resp3.text
    resp4 = client.get("/api/swarm/model-endpoints")
    assert resp4.status_code == 401, resp4.text
    resp5 = client.post("/api/drafts/generate", json={"prompt": "unauthorized"})
    assert resp5.status_code == 401, resp5.text


def test_event_internal_failure_is_not_queued_or_accepted(monkeypatch, seeded_neo4j):
    monkeypatch.setattr("assistx.swarm_routes._neo", lambda: seeded_neo4j)
    monkeypatch.setattr(seeded_neo4j, "close", lambda: None)
    monkeypatch.setattr(
        "assistx.swarm_routes.record_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    class RejectOutbox:
        def enqueue(self, event):
            raise AssertionError("server ingestion errors must not be forwarded")

    monkeypatch.setattr("assistx.swarm_routes._outbox", lambda: RejectOutbox())
    client = TestClient(app)
    response = client.post(
        "/api/events",
        json=_base_event(event_id="failed-event", idempotency_key="failed-key"),
        auth=_auth(),
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"].startswith("Event processing failed:")


def test_custom_lease_seconds_on_claim(seeded_neo4j):
    task = seeded_neo4j.get_ready_tasks()[0]
    default = seeded_neo4j.claim_task(task["id"], agent_id="agent-leasetest", capabilities=[])
    assert default["claimed"] is True
    stored = seeded_neo4j.get_task(task["id"])
    assert stored.get("lease_seconds") == 900
    assert stored["status"] == "CLAIMED"

    task2_id = seeded_neo4j.upsert_ticket(
        title="Custom lease test",
        ticket_type="task",
        status="READY",
        kind="phase2_test_lease",
    )
    custom = seeded_neo4j.claim_task(task2_id, agent_id="agent-leasetest", capabilities=[], lease_seconds=60)
    assert custom["claimed"] is True
    stored2 = seeded_neo4j.get_task(task2_id)
    assert stored2.get("lease_seconds") == 60
    now_ms = int(time.time() * 1000)
    assert stored2.get("lease_expires_at_ts", 0) > now_ms
    assert stored2.get("lease_expires_at_ts", 0) < now_ms + 120_000


def test_custom_lease_seconds_on_heartbeat(seeded_neo4j):
    task = seeded_neo4j.get_ready_tasks()[-1]
    claimed = seeded_neo4j.claim_task(task["id"], agent_id="agent-heartbeatlease", capabilities=[])
    assert claimed["claimed"] is True

    hb = seeded_neo4j.heartbeat_task(task["id"], agent_id="agent-heartbeatlease", status="RUNNING", lease_seconds=30)
    assert hb["status"] == "RUNNING"
    assert hb.get("lease_seconds") == 30
    now_ms = int(time.time() * 1000)
    expires = hb.get("lease_expires_at_ts", 0)
    assert expires > now_ms
    assert expires < now_ms + 60_000

    hb_default = seeded_neo4j.heartbeat_task(task["id"], agent_id="agent-heartbeatlease", status="RUNNING")
    assert hb_default.get("lease_seconds") == 30
    assert hb_default.get("lease_expires_at_ts", 0) > now_ms
