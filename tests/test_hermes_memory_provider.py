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
