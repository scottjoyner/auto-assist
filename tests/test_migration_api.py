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
    intent_body = r.json()
    intent_id = intent_body["intent_id"]
    assert isinstance(intent_id, str)
    assert intent_body["classification"] in {"task", "memory", "query", "cancel", "unknown"}
    assert intent_body["intent_outcome"] in {
        "actionable_task",
        "memory_capture",
        "information_query",
        "cancellation",
        "ambiguous",
    }
    assert intent_body["policy_action"] in {
        "auto_dispatch_eligible",
        "review_dispatch",
        "auto_cancel_eligible",
        "review_cancel",
        "no_dispatch",
        "needs_clarification",
    }

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


def test_intent_outcome_policy_variants(seeded_neo4j, monkeypatch):
    monkeypatch.setattr("assistx.api._neo", lambda: seeded_neo4j)
    monkeypatch.setattr(seeded_neo4j, "close", lambda: None)

    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    cancel = client.post(
        "/api/intents",
        json={"source": "voice", "text": "cancel that task", "idempotency_key": "intent-cancel-policy"},
        auth=auth,
    )
    assert cancel.status_code == 200, cancel.text
    body = cancel.json()
    assert body["classification"] == "cancel"
    assert body["intent_outcome"] == "cancellation"
    assert body["policy_action"] == "auto_cancel_eligible"

    memory = client.post(
        "/api/intents",
        json={"source": "voice", "text": "remember I prefer dark mode", "idempotency_key": "intent-memory-policy"},
        auth=auth,
    )
    assert memory.status_code == 200, memory.text
    mbody = memory.json()
    assert mbody["classification"] == "memory"
    assert mbody["intent_outcome"] == "memory_capture"
    assert mbody["policy_action"] == "no_dispatch"


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


def test_voice_event_ingestion(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    created_issues = []

    class FakePaperclip:
        def create_issue(self, **kwargs):
            with neo.driver.session() as s:
                linked_before_dispatch = s.run(
                    "MATCH (:Intent)-[:CREATED_TASK]->(:Task {id:$task_id}) "
                    "RETURN count(*) AS count",
                    {"task_id": kwargs["task_id"]},
                ).single()
                orchestrated_before_dispatch = s.run(
                    "MATCH (i:Intent)-[:CREATED_TASK]->(:Task {id:$task_id}) "
                    "WHERE i.orchestrated_at IS NOT NULL RETURN count(*) AS count",
                    {"task_id": kwargs["task_id"]},
                ).single()
            assert linked_before_dispatch["count"] == 1
            assert orchestrated_before_dispatch["count"] == 1
            created_issues.append(kwargs)
            return "voice-paperclip-issue-1"

    monkeypatch.setattr("assistx.api.get_paperclip_client", lambda: FakePaperclip())
    monkeypatch.setattr("assistx.api.PAPERCLIP_AGENT_ID", "Hermes Agent")

    with neo.driver.session() as s:
        s.run("CREATE (:SophiaCapture {capture_id:'sophia-capture-1'})").consume()

    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))
    payload = {
        "event_id": "voice-evt-1",
        "event_type": "task_created",
        "text": "Create a task to review my weekly goals",
        "source": "voice",
        "session_id": "voice-session-1",
        "client_ts": "2026-05-23T12:00:00Z",
        "metadata": {"origin": "tts", "capture_id": "sophia-capture-1"},
    }
    r = client.post("/api/voice/events", json=payload, auth=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["signal_event_id"] == "voice-evt-1"
    assert body["intent_id"]
    assert body["task_id"]
    assert created_issues[0]["assignee_id"] == "Hermes Agent"
    with neo.driver.session() as s:
        s.run(
            "MATCH (:Task {id:$task_id})-[:DISPATCHED_AS]->(d:Dispatch) "
            "REMOVE d.idempotency_key",
            {"task_id": body["task_id"]},
        ).consume()
    retry = client.post("/api/voice/events", json=payload, auth=auth)
    assert retry.status_code == 200, retry.text
    assert retry.json()["task_id"] == body["task_id"]
    assert len(created_issues) == 1
    neo.ingest_paperclip_event(
        event_type="run_completed",
        paperclip_issue_id="voice-paperclip-issue-1",
        paperclip_agent_id="Hermes Agent",
        paperclip_run_id="voice-run-1",
        event_id="voice-paperclip-complete-1",
        payload={"issue": {"status": "done"}},
    )
    with neo.driver.session() as s:
        linked = s.run(
            "MATCH (:SophiaCapture {capture_id:'sophia-capture-1'})-[:CANONICAL_CAPTURE]->"
            "(c:MediaCapture {id:'sophia-capture-1', origin:'sophia_voice'}) "
            "MATCH (t:Task {id:$task_id})-[:CREATED_FROM]->(c) "
            "RETURN count(*) AS count, t.status AS task_status",
            {"task_id": body["task_id"]},
        ).single()
    assert linked["count"] == 1
    assert linked["task_status"] == "DONE"
    completed_retry = client.post("/api/voice/events", json=payload, auth=auth)
    assert completed_retry.status_code == 200, completed_retry.text
    assert len(created_issues) == 1
    assert neo.get_task(body["task_id"])["status"] == "DONE"

    with neo.driver.session() as s:
        s.run("CREATE (:Meeting {id:'meeting-voice-1'})").consume()
    meeting = client.post(
        "/api/voice/events",
        json={
            "event_id": "voice-evt-meeting-1",
            "event_type": "meeting_transcript",
            "text": "Create a task to follow up on the meeting action items",
            "source": "sophia_voice",
            "metadata": {"meeting_id": "meeting-voice-1"},
        },
        auth=auth,
    )
    assert meeting.status_code == 200, meeting.text
    assert meeting.json()["task_id"]
    with neo.driver.session() as s:
        meeting_link = s.run(
            "MATCH (:Meeting {id:'meeting-voice-1'})-[:CREATED_TASK]->(:Task {id:$task_id}) "
            "RETURN count(*) AS count",
            {"task_id": meeting.json()["task_id"]},
        ).single()
    assert meeting_link["count"] == 1


def test_voice_event_signature_auth(monkeypatch):
    class FakeNeo:
        def create_signal_event(self, **kwargs):
            return kwargs["event_id"]

        def upsert_intent(self, **kwargs):
            return "intent-voice-signed"

        def upsert_memory_item(self, **kwargs):
            return "memory-voice-signed"

        def link_sophia_voice_records(self, **kwargs):
            return None

        def _session(self):
            class _SessCtx:
                def __enter__(self_inner):
                    class _S:
                        def run(self, *args, **kwargs):
                            class _R:
                                def single(self):
                                    return {"cancelled": 0}
                            return _R()
                    return _S()

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _SessCtx()

        def close(self):
            return None

    secret = "voice-secret-test"
    monkeypatch.setattr("assistx.api._neo", lambda: FakeNeo())
    monkeypatch.setattr("assistx.api.VOICE_WEBHOOK_SECRET", secret)

    client = TestClient(app)
    payload = {
        "event_id": "voice-evt-signed-1",
        "event_type": "tts_chunk",
        "text": "remember this",
        "source": "voice",
    }
    from assistx.api import VoiceEventIn
    import hashlib
    import hmac

    raw = VoiceEventIn(**payload).model_dump_json(exclude_none=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    r = client.post(
        "/api/voice/events",
        json=payload,
        headers={"X-Voice-Signature": f"sha256={sig}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["signal_event_id"] == "voice-evt-signed-1"


def test_voice_event_requires_auth_or_signature(monkeypatch):
    monkeypatch.setattr("assistx.api.VOICE_WEBHOOK_SECRET", "voice-secret-test")
    client = TestClient(app)
    payload = {
        "event_id": "voice-evt-noauth-1",
        "event_type": "tts_chunk",
        "text": "remember this",
        "source": "voice",
    }
    r = client.post("/api/voice/events", json=payload)
    assert r.status_code == 401, r.text


def test_ops_status_endpoint(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)

    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))
    r = client.get("/api/ops/status", auth=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "neo4j" in body and "queue" in body and "sessions" in body and "dispatches" in body and "review" in body and "feeds" in body
    assert "status" in body["neo4j"]
    assert isinstance(body["queue"]["depth"], int)
    assert isinstance(body["review"]["backlog"], int)
    assert "sla_breached" in body["review"]
    assert isinstance(body["feeds"]["total"], int)
    assert isinstance(body["feeds"]["enabled"], int)
    assert "by_status" in body["feeds"]
    assert "evaluation_suites" in body
    assert isinstance(body["evaluation_suites"]["total"], int)


def test_paperclip_event_requires_signature_secret(monkeypatch):
    class FakeNeo:
        def ingest_paperclip_event(self, **kwargs):
            return kwargs["paperclip_issue_id"]

        def close(self):
            return None

    monkeypatch.setattr("assistx.api._neo", lambda: FakeNeo())
    monkeypatch.setattr("assistx.api.PAPERCLIP_WEBHOOK_SECRET", None)

    client = TestClient(app)
    payload = {
        "event_type": "run_completed",
        "paperclip_issue_id": "issue-1",
        "paperclip_agent_id": "agent-1",
        "paperclip_run_id": "run-1",
        "event_id": "evt-1",
        "payload": {"status": "DONE"},
    }
    r = client.post("/api/paperclip/events", json=payload)
    assert r.status_code == 503, r.text


def test_paperclip_event_signature_validation(monkeypatch):
    class FakeNeo:
        def ingest_paperclip_event(self, **kwargs):
            return kwargs["paperclip_issue_id"]

        def close(self):
            return None

    secret = "paperclip-test-secret"
    monkeypatch.setattr("assistx.api._neo", lambda: FakeNeo())
    monkeypatch.setattr("assistx.api.PAPERCLIP_WEBHOOK_SECRET", secret)

    client = TestClient(app)
    payload = {
        "event_type": "run_completed",
        "paperclip_issue_id": "issue-2",
        "paperclip_agent_id": "agent-2",
        "paperclip_run_id": "run-2",
        "event_id": "evt-2",
        "payload": {"status": "DONE"},
    }

    bad = client.post(
        "/api/paperclip/events",
        json=payload,
        headers={"X-Paperclip-Signature": "sha256=bad"},
    )
    assert bad.status_code == 401, bad.text

    import hashlib
    import hmac
    from assistx.api import PaperclipEventIn

    raw = PaperclipEventIn(**payload).model_dump_json(exclude_none=True).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    good = client.post(
        "/api/paperclip/events",
        json=payload,
        headers={"X-Paperclip-Signature": f"sha256={expected}"},
    )
    assert good.status_code == 200, good.text
    assert good.json()["paperclip_issue_id"] == "issue-2"


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


def test_api_ask_sync_idempotency(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)

    calls = {"count": 0}

    def fake_answer_question(*args, **kwargs):
        calls["count"] += 1
        return {
            "answer": "ok",
            "data_preview": [],
            "cypher": "RETURN 1",
            "analysis_code": "def main(rows): return {'ok': True}",
            "computed": {"ok": True},
            "stdout": "",
            "cached": False,
            "run_id": "run-1",
        }

    monkeypatch.setattr("assistx.api.answer_question", fake_answer_question)
    store = {}
    monkeypatch.setattr("assistx.api.idemp_load", lambda key: store.get(key))
    monkeypatch.setattr("assistx.api.idemp_save", lambda key, value: store.__setitem__(key, value))

    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))
    body = {
        "question": "How many tasks are ready?",
        "mode": "sync",
        "idempotency_key": "sync-idemp-1",
    }

    r1 = client.post("/api/ask", json=body, auth=auth)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/ask", json=body, auth=auth)
    assert r2.status_code == 200, r2.text
    assert calls["count"] == 1
    assert r2.json()["answer"] == "ok"


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


def test_phase9_feeds_and_evaluations_api(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    feeds = client.get("/api/feeds", auth=auth)
    assert feeds.status_code == 200, feeds.text
    assert feeds.json()["count"] >= 1
    assert any(item["id"] == "sophia-voice-auth" for item in feeds.json()["items"])

    upsert = client.post(
        "/api/feeds",
        json={
            "id": "fundamentals-feed",
            "name": "Fundamentals Feed",
            "category": "financial",
            "endpoint": "local://fundamentals",
            "enabled": True,
            "health_status": "healthy",
        },
        auth=auth,
    )
    assert upsert.status_code == 200, upsert.text
    assert upsert.json()["ok"] is True

    feeds2 = client.get("/api/feeds", auth=auth)
    assert feeds2.status_code == 200, feeds2.text
    assert any(item["id"] == "fundamentals-feed" for item in feeds2.json()["items"])

    eval_create_1 = client.post(
        "/api/evaluations",
        json={
            "suite_name": "financial_health_daily",
            "agent_class": "financial_health_analyst",
            "status": "completed",
            "score": 0.91,
            "metadata": {"window": "1d"},
        },
        auth=auth,
    )
    assert eval_create_1.status_code == 200, eval_create_1.text
    assert eval_create_1.json()["evaluation_run_id"]

    eval_create_2 = client.post(
        "/api/evaluations",
        json={
            "suite_name": "research_quality_daily",
            "agent_class": "research_agent",
            "status": "failed",
            "score": 0.42,
        },
        auth=auth,
    )
    assert eval_create_2.status_code == 200, eval_create_2.text

    evals_all = client.get("/api/evaluations", auth=auth)
    assert evals_all.status_code == 200, evals_all.text
    assert evals_all.json()["count"] >= 2

    evals_failed = client.get("/api/evaluations?status=failed", auth=auth)
    assert evals_failed.status_code == 200, evals_failed.text
    assert all(item["status"] == "failed" for item in evals_failed.json()["items"])

    suites = client.get("/api/evaluations/suites", auth=auth)
    assert suites.status_code == 200, suites.text
    assert suites.json()["count"] >= 2
    assert any(item["name"] == "sophia_auth_quality_daily" for item in suites.json()["items"])

    suite_upsert = client.post(
        "/api/evaluations/suites",
        json={
            "name": "sophia_policy_routing_daily",
            "agent_class": "voice_policy_analyst",
            "enabled": True,
            "cadence": "daily",
            "threshold": 0.81,
            "description": "Validate auth-state to response-voice policy routing.",
        },
        auth=auth,
    )
    assert suite_upsert.status_code == 200, suite_upsert.text
    assert suite_upsert.json()["ok"] is True


def test_sophia_event_ingestion(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    created_issues = []

    class FakePaperclip:
        def create_issue(self, **kwargs):
            created_issues.append(kwargs)
            return "sophia-paperclip-issue-1"

    monkeypatch.setattr("assistx.api.get_paperclip_client", lambda: FakePaperclip())
    monkeypatch.setattr("assistx.api.PAPERCLIP_AGENT_ID", "Hermes Agent")
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    payload = {
        "event_id": "sophia-evt-1",
        "event_type": "intent",
        "session_id": "sophia-session-1",
        "transcript_text": "Create a follow-up task for my meeting notes",
        "auth_state": "authenticated_scott",
        "speaker_identity": "scott",
        "speaker_confidence": 0.93,
        "policy_version": "v1",
        "payload": {"source": "ws"},
    }
    r = client.post("/api/sophia/events", json=payload, auth=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["signal_event_id"] == "sophia-evt-1"
    assert body["intent_id"]
    assert body["queue_class"] == "interactive"
    assert body["routing_policy_fingerprint"]
    assert created_issues
    assert created_issues[0]["assignee_id"] == "Hermes Agent"
    assert any(
        d.get("paperclip_issue_id") == "sophia-paperclip-issue-1"
        for d in neo.list_dispatches(status="OPEN", limit=50)
    )

    anomaly = {
        "event_id": "sophia-evt-2",
        "event_type": "voice_chat",
        "session_id": "sophia-session-2",
        "transcript_text": "Summarize what was said",
        "auth_state": "unknown_unverified",
        "speaker_identity": "unknown",
        "speaker_confidence": 0.22,
        "policy_version": "v1",
        "payload": {},
    }
    r2 = client.post("/api/sophia/events", json=anomaly, auth=auth)
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["queue_class"] == "critical"
    assert b2["incident_id"]

    summary = client.get("/api/sophia/summary?limit=50", auth=auth)
    assert summary.status_code == 200, summary.text
    sj = summary.json()
    assert sj["sample_size"] >= 2
    assert sj["auth_states"]["authenticated_scott"] >= 1
    assert sj["auth_states"]["unknown_unverified"] >= 1
    assert sj["by_queue_class"]["critical"] >= 1
    assert "routing_policy" in sj
    assert sj["routing_policy_fingerprint"]


def test_sophia_routing_policy_override(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    monkeypatch.setenv(
        "ASSISTX_SOPHIA_ROUTING_POLICY",
        '{"default_queue_class":"batch","by_auth_state":{"authenticated_scott":"interactive","unknown_unverified":"critical"},"by_event_type_prefix":{"intent":"interactive","meeting":"batch"}}',
    )
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    r1 = client.post(
        "/api/sophia/events",
        json={
            "event_id": "sophia-evt-policy-1",
            "event_type": "intent",
            "session_id": "sophia-policy-1",
            "transcript_text": "Do a quick follow up",
            "auth_state": "authenticated_scott",
        },
        auth=auth,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["queue_class"] == "interactive"

    r2 = client.post(
        "/api/sophia/events",
        json={
            "event_id": "sophia-evt-policy-2",
            "event_type": "meeting_process",
            "session_id": "sophia-policy-2",
            "transcript_text": "process meeting notes",
            "auth_state": "authenticated_scott",
        },
        auth=auth,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["queue_class"] == "interactive"  # auth-state override takes precedence

    policy = client.get("/api/sophia/policy", auth=auth)
    assert policy.status_code == 200, policy.text
    assert policy.json()["routing_policy"]["default_queue_class"] == "batch"
    assert policy.json()["routing_policy_fingerprint"]


def test_sophia_policy_change_incident_tracking(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    monkeypatch.setenv(
        "ASSISTX_SOPHIA_ROUTING_POLICY",
        '{"default_queue_class":"interactive","by_auth_state":{"unknown_unverified":"critical"},"by_event_type_prefix":{"meeting":"batch"}}',
    )
    p1 = client.get("/api/sophia/policy", auth=auth)
    assert p1.status_code == 200, p1.text
    fp1 = p1.json()["routing_policy_fingerprint"]

    monkeypatch.setenv(
        "ASSISTX_SOPHIA_ROUTING_POLICY",
        '{"default_queue_class":"batch","by_auth_state":{"unknown_unverified":"critical"},"by_event_type_prefix":{"meeting":"batch"}}',
    )
    p2 = client.get("/api/sophia/policy", auth=auth)
    assert p2.status_code == 200, p2.text
    fp2 = p2.json()["routing_policy_fingerprint"]
    assert fp2 != fp1
    assert p2.json()["policy_change_incident_id"]

    incidents = client.get("/api/workflows/sophia-policy/incidents?limit=20", auth=auth)
    assert incidents.status_code == 200, incidents.text
    assert incidents.json()["count"] >= 1


def test_phase8_workflow_ops_endpoints(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    # Seed some queue-class tasks
    batch_id = neo.upsert_ticket(
        title="Batch workflow task",
        ticket_type="task",
        status="READY",
        kind="workflow_step",
        payload={"queue_class": "batch"},
        idempotency_key="wf-batch-1",
    )
    critical_id = neo.upsert_ticket(
        title="Critical workflow task",
        ticket_type="task",
        status="RUNNING",
        kind="workflow_step",
        payload={"queue_class": "critical"},
        idempotency_key="wf-critical-1",
    )
    assert batch_id and critical_id

    q = client.get("/api/workflows/queue", auth=auth)
    assert q.status_code == 200, q.text
    qj = q.json()
    assert "by_queue_class" in qj and "control" in qj
    assert qj["by_queue_class"]["batch"] >= 1
    assert qj["by_queue_class"]["critical"] >= 1

    # Drain mode should block non-critical claims
    ctl_drain = client.post("/api/workflows/control", json={"action": "drain"}, auth=auth)
    assert ctl_drain.status_code == 200, ctl_drain.text
    polled = client.get("/api/agent/tasks?status=READY&agent_id=agent-8&limit=50", auth=auth)
    assert polled.status_code == 200, polled.text
    for item in polled.json()["items"]:
        assert item["queue_class"] == "critical"
    claim_block = client.post(
        f"/api/tasks/{batch_id}/claim",
        json={"agent_id": "agent-8", "capabilities": ["terminal"], "session_id": "sess-8"},
        auth=auth,
    )
    assert claim_block.status_code == 409, claim_block.text
    assert claim_block.json()["detail"]["reason"] == "drain_mode_block"

    # Critical claims are still allowed in drain mode
    critical_ready_id = neo.upsert_ticket(
        title="Critical ready task",
        ticket_type="task",
        status="READY",
        kind="workflow_step",
        payload={"queue_class": "critical"},
        idempotency_key="wf-critical-ready-1",
    )
    claim_ok = client.post(
        f"/api/tasks/{critical_ready_id}/claim",
        json={"agent_id": "agent-8", "capabilities": ["terminal"], "session_id": "sess-8b"},
        auth=auth,
    )
    assert claim_ok.status_code == 200, claim_ok.text
    assert claim_ok.json()["claimed"] is True

    slo = client.get("/api/workflows/slo?window_hours=24", auth=auth)
    assert slo.status_code == 200, slo.text
    sj = slo.json()
    assert sj["window_hours"] == 24
    assert "p95_start_latency_s" in sj and "success_rate" in sj

    ctl1 = client.post("/api/workflows/control", json={"action": "drain"}, auth=auth)
    assert ctl1.status_code == 200, ctl1.text
    assert ctl1.json()["control"]["mode"] == "drain"

    ctl2 = client.post(
        "/api/workflows/control",
        json={"action": "set_limits", "max_concurrent_workflows": 7, "max_batch_backlog": 99},
        auth=auth,
    )
    assert ctl2.status_code == 200, ctl2.text
    assert ctl2.json()["control"]["max_concurrent_workflows"] == 7
    assert ctl2.json()["control"]["max_batch_backlog"] == 99

    budget = client.post(
        f"/api/workflows/{batch_id}/budget/update",
        json={"token_budget": 25000, "time_budget_s": 1800, "retry_budget": 3},
        auth=auth,
    )
    assert budget.status_code == 200, budget.text
    assert budget.json()["updated"] is True

    replan = client.post(
        f"/api/workflows/{batch_id}/replan",
        json={"reason": "verification_failed", "severity": "warning"},
        auth=auth,
    )
    assert replan.status_code == 200, replan.text
    assert replan.json()["replan_requested"] is True

    incidents = client.get(f"/api/workflows/{batch_id}/incidents", auth=auth)
    assert incidents.status_code == 200, incidents.text
    assert incidents.json()["count"] >= 1

    ctl_resume = client.post("/api/workflows/control", json={"action": "resume"}, auth=auth)
    assert ctl_resume.status_code == 200, ctl_resume.text
    assert ctl_resume.json()["control"]["mode"] == "resume"

    ops = client.get("/api/ops/status", auth=auth)
    assert ops.status_code == 200, ops.text
    assert "workflow" in ops.json()
    assert "escalation_backlog" in ops.json()["workflow"]


def test_phase8_retry_budget_dead_letter(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    task_id = neo.upsert_ticket(
        title="Retry budget bounded workflow step",
        ticket_type="task",
        status="RUNNING",
        kind="workflow_step",
        payload={"queue_class": "batch"},
        idempotency_key="wf-dead-letter-1",
    )
    assert task_id

    budget = client.post(
        f"/api/workflows/{task_id}/budget/update",
        json={"retry_budget": 0},
        auth=auth,
    )
    assert budget.status_code == 200, budget.text

    done = client.post(
        f"/api/tasks/{task_id}/complete",
        json={"agent_id": "agent-deadletter", "status": "FAILED", "summary": "tool timeout"},
        auth=auth,
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["dead_letter_incident_id"]
    assert body["task"]["status"] == "REVIEW"
    assert body["task"]["dead_lettered"] is True
    assert body["task"]["dead_letter_reason"] == "retry_budget_exhausted"

    incidents = client.get(f"/api/workflows/{task_id}/incidents?limit=20", auth=auth)
    assert incidents.status_code == 200, incidents.text
    assert any(i.get("incident_type") == "retry_budget_exhausted" for i in incidents.json()["items"])


def test_review_queue_actions(seeded_neo4j, monkeypatch):
    neo = seeded_neo4j
    monkeypatch.setattr("assistx.api._neo", lambda: neo)
    monkeypatch.setattr(neo, "close", lambda: None)
    client = TestClient(app)
    auth = (os.getenv("BASIC_AUTH_USER", "neo4j"), os.getenv("BASIC_AUTH_PASS", "livelongandprosper"))

    review_id = neo.upsert_ticket(
        title="Review intent: build weekly status dashboard",
        ticket_type="chore",
        status="REVIEW",
        kind="intent_review",
        payload={
            "source_intent": "intent-review-queue-1",
            "source_text": "Build a weekly status dashboard from recent tasks",
            "policy_action": "review_dispatch",
        },
        idempotency_key="review-ticket-1",
    )

    r = client.get("/api/review/tasks", auth=auth)
    assert r.status_code == 200, r.text
    assert any(item["id"] == review_id for item in r.json()["items"])

    filtered = client.get("/api/review/tasks?policy_action=review_dispatch", auth=auth)
    assert filtered.status_code == 200, filtered.text
    assert any(item["id"] == review_id for item in filtered.json()["items"])

    approve = client.post(
        f"/api/review/tasks/{review_id}/approve",
        json={
            "note": "Looks good, proceed",
            "auto_dispatch": False,
            "priority": "HIGH",
            "target": {"paperclip_agent_id": "agent-review-target"},
        },
        auth=auth,
    )
    assert approve.status_code == 200, approve.text
    out = approve.json()
    assert out["decision"] == "approved"
    assert out["created_task_id"]

    review_after = neo.get_task(review_id)
    created_task = neo.get_task(out["created_task_id"])
    assert review_after["status"] == "DONE"
    assert review_after["review_decision"] == "approved"
    assert created_task["status"] == "READY"
    assert created_task["target_agent_id"] == "agent-review-target"

    reject_id = neo.upsert_ticket(
        title="Review intent: risky destructive action",
        ticket_type="chore",
        status="REVIEW",
        kind="intent_review",
        payload={"source_text": "Delete all history"},
        idempotency_key="review-ticket-2",
    )
    reject = client.post(
        f"/api/review/tasks/{reject_id}/reject",
        json={"note": "Denied by operator"},
        auth=auth,
    )
    assert reject.status_code == 200, reject.text
    assert reject.json()["decision"] == "rejected"
    assert neo.get_task(reject_id)["status"] == "CANCELLED"

    clarify_id = neo.upsert_ticket(
        title="Review intent: unclear request",
        ticket_type="chore",
        status="REVIEW",
        kind="intent_review",
        payload={"source_text": "do the thing"},
        idempotency_key="review-ticket-3",
    )
    clarify = client.post(
        f"/api/review/tasks/{clarify_id}/clarify",
        json={"note": "Need more detail on output format"},
        auth=auth,
    )
    assert clarify.status_code == 200, clarify.text
    assert clarify.json()["decision"] == "clarification_requested"
    clarify_task = neo.get_task(clarify_id)
    assert clarify_task["status"] == "REVIEW"
    assert clarify_task["review_decision"] == "clarification_requested"

    audit = client.get("/api/review/audit?limit=20", auth=auth)
    assert audit.status_code == 200, audit.text
    items = audit.json()["items"]
    assert any(i["review_task_id"] == review_id and i["review_decision"] == "approved" for i in items)
    assert any(i["review_task_id"] == reject_id and i["review_decision"] == "rejected" for i in items)
    assert any(i["review_task_id"] == clarify_id and i["review_decision"] == "clarification_requested" for i in items)

    first_page = client.get("/api/review/audit?limit=1", auth=auth)
    assert first_page.status_code == 200, first_page.text
    fp = first_page.json()
    assert fp["count"] == 1
    assert fp.get("next_cursor")
    second_page = client.get(f"/api/review/audit?limit=2&cursor={fp['next_cursor']}", auth=auth)
    assert second_page.status_code == 200, second_page.text
    sp_items = second_page.json()["items"]
    assert all(i["review_task_id"] != fp["items"][0]["review_task_id"] for i in sp_items)

    summary = client.get("/api/review/audit/summary", auth=auth)
    assert summary.status_code == 200, summary.text
    summary_json = summary.json()
    assert summary_json["window_hours"] == 24
    assert summary_json["total_decisions"] >= 3
    assert summary_json["by_decision"].get("approved", 0) >= 1
