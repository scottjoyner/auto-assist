import os

from fastapi.testclient import TestClient
from assistx.api import app


def test_api_intent_and_context_packet(seeded_neo4j, monkeypatch):
    # Force the API to use the test Neo4j client rather than environment defaults.
    monkeypatch.setattr("assistx.api._neo", lambda: seeded_neo4j)
    monkeypatch.setattr(seeded_neo4j, "close", lambda: None)

    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    intent_payload = {
        "source": "voice",
        "text": "Summarize the latest task",
        "idempotency_key": "intent-test-key",
        "client_ts": "2026-05-22T12:00:00Z",
        "metadata": {"user": "pytest"},
    }
    r = client.post("/api/intents", json=intent_payload, auth=auth)
    assert r.status_code == 200, r.text
    intent_id = r.json()["intent_id"]
    assert isinstance(intent_id, str)

    context_payload = {
        "query": "Need bounded context for task review",
        "task_id": seeded_neo4j.get_ready_tasks()[0]["id"],
        "include_sources": ["memory", "knowledge"],
    }
    r2 = client.post("/api/brain/context", json=context_payload, auth=auth)
    assert r2.status_code == 200, r2.text
    data = r2.json()["context_packet"]
    assert data["query"] == context_payload["query"]
    assert data["max_items"] == 20
    assert isinstance(data["references"], list)
    assert any(ref["node"].get("title") == "Review item" for ref in data["references"])

    packet_id = data["id"]
    r3 = client.get(f"/api/context-packets/{packet_id}", auth=auth)
    assert r3.status_code == 200, r3.text
    packet_data = r3.json()["context_packet"]
    assert packet_data["id"] == packet_id
    assert packet_data["query"] == context_payload["query"]


def test_dispatch_and_session_endpoints(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    task = seeded_neo4j.get_ready_tasks()[0]
    dispatch_payload = {
        "task_id": task["id"],
        "target": {
            "paperclip_agent_id": "agent-123",
            "capabilities": ["terminal", "code_execution"],
        },
        "priority": "HIGH",
        "idempotency_key": "dispatch-test-key",
    }
    r = client.post("/api/dispatch", json=dispatch_payload, auth=auth)
    assert r.status_code == 200, r.text
    dispatch_id = r.json()["dispatch_id"]
    assert isinstance(dispatch_id, str)

    # verify dispatch record exists
    dispatches = neo.list_dispatches(status="OPEN", limit=10)
    assert any(d["id"] == dispatch_id for d in dispatches)

    # update a session
    session_payload = {
        "paperclip_agent_id": "agent-123",
        "hermes_session_id": "hermes-abc",
        "agent_identity": "hermes-local",
        "device_id": "device-1",
        "platform": "linux",
        "metadata": {"region": "us-west"},
    }
    r2 = client.post("/api/sessions/session-1", json=session_payload, auth=auth)
    assert r2.status_code == 200, r2.text
    assert r2.json()["session_id"] == "session-1"

    # write a memory item
    memory_payload = {
        "kind": "note",
        "text": "Hermes observed a new fact.",
        "source": "hermes",
        "session_id": "session-1",
    }
    r3 = client.post("/api/memory/items", json=memory_payload, auth=auth)
    assert r3.status_code == 200, r3.text
    memory_item_id = r3.json()["memory_item_id"]
    assert isinstance(memory_item_id, str)

    # signal event
    signal_payload = {
        "event_id": "signal-1",
        "event_type": "session_updated",
        "payload": {"status": "ready"},
        "session_id": "session-1",
        "paperclip_issue_id": None,
        "paperclip_run_id": None,
    }
    r4 = client.post("/api/brain/signals", json=signal_payload, auth=auth)
    assert r4.status_code == 200, r4.text
    assert r4.json()["signal_event_id"] == "signal-1"


def test_task_trigger_lifecycle(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)

    task = neo.get_ready_tasks()[0]
    with neo.driver.session() as s:
        s.run(
            "MATCH (t:Task {id:$id}) "
            "SET t.required_capabilities=$caps, t.priority_rank=10",
            {"id": task["id"], "caps": ["code_execution"]},
        )

    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    r = client.get(
        "/api/agent/tasks",
        params={"capabilities": "code_execution", "agent_id": "agent-1"},
        auth=auth,
    )
    assert r.status_code == 200, r.text
    assert any(item["id"] == task["id"] for item in r.json()["items"])

    claim_payload = {
        "agent_id": "agent-1",
        "capabilities": ["code_execution"],
        "session_id": "session-trigger-1",
        "idempotency_key": "claim-trigger-1",
    }
    r2 = client.post(f"/api/tasks/{task['id']}/claim", json=claim_payload, auth=auth)
    assert r2.status_code == 200, r2.text
    assert r2.json()["claimed"] is True
    assert r2.json()["task"]["status"] == "CLAIMED"

    r2_repeat = client.post(f"/api/tasks/{task['id']}/claim", json=claim_payload, auth=auth)
    assert r2_repeat.status_code == 200, r2_repeat.text
    assert r2_repeat.json()["idempotent"] is True

    r2_conflict = client.post(
        f"/api/tasks/{task['id']}/claim",
        json={"agent_id": "agent-2", "capabilities": ["code_execution"]},
        auth=auth,
    )
    assert r2_conflict.status_code == 409, r2_conflict.text

    context = client.post(
        "/api/brain/context",
        json={
            "query": "Review item",
            "task_id": task["id"],
            "session_id": "session-trigger-1",
            "include_sources": ["orchestration", "memory", "knowledge"],
        },
        auth=auth,
    )
    assert context.status_code == 200, context.text
    assert any(ref["source_type"] == "Task" for ref in context.json()["context_packet"]["references"])

    r3 = client.post(
        f"/api/tasks/{task['id']}/heartbeat",
        json={
            "agent_id": "agent-1",
            "status": "RUNNING",
            "session_id": "session-trigger-1",
            "metadata": {"progress": "started"},
        },
        auth=auth,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["task"]["status"] == "RUNNING"

    r4 = client.post(
        f"/api/tasks/{task['id']}/complete",
        json={
            "agent_id": "agent-1",
            "status": "DONE",
            "session_id": "session-trigger-1",
            "summary": "Trigger lifecycle completed.",
            "result": {"ok": True},
        },
        auth=auth,
    )
    assert r4.status_code == 200, r4.text
    body = r4.json()["task"]
    assert body["status"] == "DONE"
    assert body["run_id"]
    assert body["memory_item_id"]


def test_ticket_hierarchy_and_paperclip_dispatch(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)

    class FakePaperclip:
        def create_issue(self, **kwargs):
            assert kwargs["task_id"]
            assert kwargs["context_packet_id"]
            assert kwargs["capabilities"] == ["code_execution"]
            return "paperclip-issue-1"

    monkeypatch.setattr("assistx.api.get_paperclip_client", lambda: FakePaperclip())

    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    epic = client.post(
        "/api/tickets",
        json={"title": "Migration epic", "ticket_type": "epic", "status": "READY"},
        auth=auth,
    )
    assert epic.status_code == 200, epic.text
    epic_id = epic.json()["ticket_id"]

    story = client.post(
        "/api/tickets",
        json={
            "title": "Wire Paperclip story",
            "ticket_type": "story",
            "status": "READY",
            "parent_id": epic_id,
            "required_capabilities": ["code_execution"],
        },
        auth=auth,
    )
    assert story.status_code == 200, story.text
    story_id = story.json()["ticket_id"]

    tree = client.get(f"/api/tickets/{epic_id}/tree", auth=auth)
    assert tree.status_code == 200, tree.text
    assert any(child["id"] == story_id for child in tree.json()["children"])

    dispatch = client.post(
        "/api/dispatch",
        json={
            "task_id": story_id,
            "target": {"paperclip_agent_id": "agent-1", "capabilities": ["code_execution"]},
            "priority": "HIGH",
        },
        auth=auth,
    )
    assert dispatch.status_code == 200, dispatch.text
    assert dispatch.json()["paperclip_issue_id"] == "paperclip-issue-1"
    assert dispatch.json()["context_packet_id"]


def test_ask_deliverable_breakdown(seeded_neo4j):
    neo = seeded_neo4j

    deliverable = neo.create_deliverable_from_ask(
        question="Build a graph-first task orchestration update",
        answer_id="answer-1",
        mode="async",
        user="pytest",
        idempotency_key="ask-deliverable-1",
    )

    assert deliverable["intent_id"]
    assert deliverable["deliverable_id"]
    assert deliverable["epic_id"]
    assert deliverable["story_id"]
    assert deliverable["task_id"]

    tree = neo.get_ticket_tree(deliverable["deliverable_id"])
    assert tree["ticket"]["ticket_type"] == "deliverable"
    assert any(child["ticket_type"] == "epic" for child in tree["children"])
    assert any(child["ticket_type"] == "story" for child in tree["children"])
    assert any(child["ticket_type"] == "task" for child in tree["children"])

    completed = neo.complete_deliverable(
        deliverable_id=deliverable["deliverable_id"],
        answer_id="answer-1",
        status="DONE",
        summary="Deliverable completed.",
        result={"ok": True},
    )

    assert completed["status"] == "DONE"
    assert completed["event_id"]


def test_command_center_intents(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    client.post("/api/intents", json={"source": "voice", "text": "Intent one", "idempotency_key": "cc-intent-1"}, auth=auth)
    client.post("/api/intents", json={"source": "ui", "text": "Intent two", "idempotency_key": "cc-intent-2"}, auth=auth)

    r = client.get("/api/intents", auth=auth)
    assert r.status_code == 200
    assert r.json()["count"] >= 2

    r = client.get("/api/intents?source=voice", auth=auth)
    assert r.status_code == 200
    assert all(i["source"] == "voice" for i in r.json()["items"])

    intent_id = r.json()["items"][0]["id"]
    r = client.get(f"/api/intents/{intent_id}", auth=auth)
    assert r.status_code == 200
    assert r.json()["intent"]["id"] == intent_id


def test_command_center_memory(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    client.post("/api/memory/items", json={"kind": "note", "text": "Memory one", "source": "hermes"}, auth=auth)
    client.post("/api/memory/items", json={"kind": "fact", "text": "Memory two", "source": "voice"}, auth=auth)

    r = client.get("/api/memory", auth=auth)
    assert r.status_code == 200
    assert r.json()["count"] >= 2

    r = client.get("/api/memory?kind=fact", auth=auth)
    assert r.status_code == 200
    assert all(m["kind"] == "fact" for m in r.json()["items"])

    memory_id = r.json()["items"][0]["id"]
    r = client.get(f"/api/memory/{memory_id}", auth=auth)
    assert r.status_code == 200
    assert r.json()["memory"]["id"] == memory_id


def test_command_center_devices(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)

    neo.upsert_agent_device("device-cc-1", hostname="host1", platform="linux", capabilities=["code"])
    neo.upsert_agent_device("device-cc-2", hostname="host2", platform="macos", capabilities=["web"])

    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    r = client.get("/api/devices", auth=auth)
    assert r.status_code == 200
    assert r.json()["count"] >= 2

    r = client.get("/api/devices/device-cc-1", auth=auth)
    assert r.status_code == 200
    assert r.json()["device"]["id"] == "device-cc-1"
    assert r.json()["device"]["hostname"] == "host1"


def test_command_center_task_controls(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    task = neo.get_ready_tasks()[0]

    r = client.post(f"/api/tasks/{task['id']}/cancel", auth=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"

    assert neo.get_task(task["id"])["status"] == "CANCELLED"


def test_command_center_reassign(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    task = neo.get_ready_tasks()[0]
    dispatch = client.post(
        "/api/dispatch",
        json={"task_id": task["id"], "target": {"paperclip_agent_id": "agent-old"}, "priority": "MEDIUM"},
        auth=auth,
    )
    dispatch_id = dispatch.json()["dispatch_id"]

    r = client.post(
        f"/api/dispatches/{dispatch_id}/reassign",
        json={"paperclip_agent_id": "agent-new"},
        auth=auth,
    )
    assert r.status_code == 200
    assert r.json()["reassigned"] is True
