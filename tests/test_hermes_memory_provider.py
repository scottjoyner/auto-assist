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
