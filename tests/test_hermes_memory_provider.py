from assistx.agents.hermes_memory_provider import HermesMemoryProvider


def test_hermes_memory_provider_prefetch(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, auth, timeout):
        captured["url"] = url
        captured["json"] = json
        return type("R", (), {"json": lambda self: {"context_packet": {"id": "cp1", "query": json["query"], "references": []}}, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("assistx.agents.hermes_memory_provider.requests.post", fake_post)
    provider = HermesMemoryProvider(base_url="http://localhost:8000", auth=("neo4j", "livelongandprosper"))

    packet = provider.prefetch("foo query", task_id="task-1")

    assert packet["id"] == "cp1"
    assert packet["query"] == "foo query"
    assert captured["url"] == "http://localhost:8000/api/brain/context"
    assert captured["json"]["task_id"] == "task-1"


def test_hermes_memory_provider_write_memory(monkeypatch):
    def fake_post(url, json, headers, auth, timeout):
        assert url.endswith("/api/memory/items")
        assert json["kind"] == "note"
        return type("R", (), {"json": lambda self: {"memory_item_id": "mem1"}, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("assistx.agents.hermes_memory_provider.requests.post", fake_post)
    provider = HermesMemoryProvider(base_url="http://localhost:8000")
    memory_id = provider.write_memory("note", "hello", "hermes", session_id="sess1")
    assert memory_id == "mem1"


def test_hermes_memory_provider_signal_event(monkeypatch):
    def fake_post(url, json, headers, auth, timeout):
        assert url.endswith("/api/brain/signals")
        assert json["event_type"] == "test_event"
        return type("R", (), {"json": lambda self: {"signal_event_id": "sig1"}, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("assistx.agents.hermes_memory_provider.requests.post", fake_post)
    provider = HermesMemoryProvider(base_url="http://localhost:8000")
    signal_id = provider.signal_event("sig-1", "test_event", {"key": "val"}, session_id="sess1")
    assert signal_id == "sig1"


def test_hermes_memory_provider_update_session(monkeypatch):
    def fake_post(url, json, headers, auth, timeout):
        assert url.endswith("/api/sessions/sess-1")
        assert json["paperclip_agent_id"] == "agent-1"
        return type("R", (), {"json": lambda self: {"session_id": "sess-1"}, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("assistx.agents.hermes_memory_provider.requests.post", fake_post)
    provider = HermesMemoryProvider(base_url="http://localhost:8000")
    session_id = provider.update_session("sess-1", paperclip_agent_id="agent-1", platform="linux")
    assert session_id == "sess-1"


def test_hermes_memory_provider_with_token(monkeypatch):
    def fake_post(url, json, headers, auth, timeout):
        assert headers.get("x-api-token") == "my-token"
        return type("R", (), {"json": lambda self: {"context_packet": {"id": "cp1"}}, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("assistx.agents.hermes_memory_provider.requests.post", fake_post)
    provider = HermesMemoryProvider(base_url="http://localhost:8000", api_token="my-token")
    packet = provider.prefetch("test")
    assert packet["id"] == "cp1"


def test_hermes_memory_provider_system_prompt_block():
    provider = HermesMemoryProvider(base_url="http://localhost:8000")
    block = provider.system_prompt_block()
    assert "graph memory" in block
    assert "prefetch" in block
    assert "write_memory" in block


def test_hermes_memory_provider_sync_turn(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, auth, timeout):
        captured["url"] = url
        captured["json"] = json
        return type("R", (), {"json": lambda self: {"signal_event_id": json.get("event_id", "sig-new")}, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("assistx.agents.hermes_memory_provider.requests.post", fake_post)
    provider = HermesMemoryProvider(base_url="http://localhost:8000")
    sig_id = provider.sync_turn("sess-1", "user said", "assistant replied", task_id="task-1")
    assert sig_id
    assert captured["json"]["event_type"] == "turn_sync"
    assert captured["json"]["payload"]["user_text"] == "user said"
    assert captured["json"]["payload"]["assistant_text"] == "assistant replied"
    assert captured["json"]["session_id"] == "sess-1"


def test_hermes_memory_provider_on_delegation(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, auth, timeout):
        captured["url"] = url
        captured["json"] = json
        return type("R", (), {"json": lambda self: {"signal_event_id": json.get("event_id", "sig-del")}, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("assistx.agents.hermes_memory_provider.requests.post", fake_post)
    provider = HermesMemoryProvider(base_url="http://localhost:8000")
    sig_id = provider.on_delegation("sess-1", "child-task-1", {"ok": True})
    assert sig_id
    assert captured["json"]["event_type"] == "delegation"
    assert captured["json"]["payload"]["child_task_id"] == "child-task-1"
    assert captured["json"]["session_id"] == "sess-1"


def test_hermes_memory_provider_on_session_switch(monkeypatch):
    captured = []

    def fake_post(url, json, headers, auth, timeout):
        captured.append({"url": url, "json": json})
        return type("R", (), {"json": lambda self: {"session_id": "sess-new", "signal_event_id": json.get("event_id", "sig-switch")}, "raise_for_status": lambda self: None})()

    monkeypatch.setattr("assistx.agents.hermes_memory_provider.requests.post", fake_post)
    provider = HermesMemoryProvider(base_url="http://localhost:8000")
    sig_id = provider.on_session_switch("sess-new", previous_session_id="sess-old", reason="compression")
    assert sig_id
    # first call: update_session with previous_session_id metadata
    assert captured[0]["url"].endswith("/api/sessions/sess-new")
    assert captured[0]["json"]["metadata"]["previous_session_id"] == "sess-old"
    assert captured[0]["json"]["metadata"]["switch_reason"] == "compression"
    # second call: signal_event for session_switch
    assert captured[1]["json"]["event_type"] == "session_switch"
    assert captured[1]["json"]["payload"]["previous_session_id"] == "sess-old"
    assert captured[1]["json"]["payload"]["reason"] == "compression"
