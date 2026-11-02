"""The authenticated READY feed must not 500 on legacy numeric priority data."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from assistx.neo4j_client import Neo4jClient


class _QuerySession:
    def __init__(self, tasks):
        self.tasks = tasks
        self.queries = []

    def run(self, query, parameters):
        self.queries.append((query, parameters))
        return [{"t": task} for task in self.tasks]


def test_mixed_priority_ready_tasks_sort_safely_without_promotion(monkeypatch):
    # A numeric-priority READY Task exists on the real graph (Nov 2, 2026).
    # This caused the live API to raise AttributeError: int.upper().
    tasks = [
        {"id": "legacy-numeric", "status": "READY", "priority": 100, "created_at_ts": 1},
        {"id": "low", "status": "READY", "priority": "LOW", "created_at_ts": 2},
        {"id": "critical", "status": "READY", "priority": "critical", "created_at_ts": 3},
        {"id": "float", "status": "READY", "priority": 0.1, "created_at_ts": 4},
        {"id": "blank", "status": "READY", "priority": None, "created_at_ts": 5},
    ]
    s = _QuerySession(tasks)
    client = Neo4jClient.__new__(Neo4jClient)

    @contextmanager
    def fake_session():
        yield s

    monkeypatch.setattr(client, "_session", fake_session)
    result = client.list_agent_tasks(status="READY",limit=20)
    assert [t["id"] for t in result] == [
        "critical", "low", "legacy-numeric", "float", "blank"
    ]
    assert result[2]["priority"] == 100  # No silent data migration.
    assert len(s.queries) == 1
    assert s.queries[0][1]["status"] == "READY"


def test_agent_scoped_feed_preserves_target_and_capability_filter(monkeypatch):
    tasks = [
        {"id": "numeric-mine", "status": "READY", "priority": 2,
         "target_agent_id": "worker-a", "required_capabilities": ["code"]},
        {"id": "numeric-other", "status": "READY", "priority": 3,
         "target_agent_id": "worker-b", "required_capabilities": ["code"]},
        {"id": "missing-cap", "status": "READY", "priority": 4,
         "required_capabilities": ["gpu"]},
        {"id": "no-priority", "status": "READY", "priority": None,
         "required_capabilities": ["code"]},
    ]
    s = _QuerySession(tasks)
    client = Neo4jClient.__new__(Neo4jClient)

    @contextmanager
    def fake_session():
        yield s

    monkeypatch.setattr(client, "_session", fake_session)
    result = client.list_agent_tasks(status="READY",agent_id="worker-a",capabilities=["code"])
    assert {t["id"] for t in result} == {"numeric-mine", "no-priority"}
    assert s.queries[0][1]["caps"] == ["code"]


def test_ready_agent_http_endpoint_survives_numeric_priority(monkeypatch):
    from fastapi.testclient import TestClient
    from assistx import api

    tasks = [
        {"id": "numeric-live", "status": "READY", "priority": 77,
         "created_at_ts": 1, "required_capabilities": [], "payload_json": "{}"},
        {"id": "high-live", "status": "READY", "priority": "HIGH",
         "created_at_ts": 2, "required_capabilities": [], "payload_json": "{}"},
    ]
    s = _QuerySession(tasks)
    neo = Neo4jClient.__new__(Neo4jClient)

    @contextmanager
    def fake_session():
        yield s

    monkeypatch.setattr(neo, "_session", fake_session)
    monkeypatch.setattr(api, "_neo_fleet", lambda: neo)
    monkeypatch.setattr(api, "_workflow_runtime_snapshot", lambda _neo: {
        "running": 0, "interactive_ready": 0, "critical_ready": 0,
        "batch_ready_direct": 0,
    })
    monkeypatch.setattr(api, "_get_workflow_control", lambda: {
        "mode": "resume", "max_concurrent_workflows": 20, "max_batch_backlog": 200,
    })
    api.app.dependency_overrides[api.auth] = lambda: "test-readonly-user"
    try:
        # Do not invoke application lifespan/start production controllers in
        # this isolated read-only route regression. The mocked route is enough.
        client = TestClient(api.app)
        try:
            response = client.get("/api/agent/tasks?status=READY&limit=20")
        finally:
            client.close()
        assert response.status_code == 200, response.text[:200]
        body = response.json()
        assert body["count"] == 2
        assert [t["id"] for t in body["items"]] == ["high-live", "numeric-live"]
    finally:
        api.app.dependency_overrides.pop(api.auth, None)
