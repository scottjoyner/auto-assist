"""API surface for the harness evolution page: /harness renders, and
/api/harness/evolution serves the snapshot from the Neo4j row shapes."""

import json

from fastapi.testclient import TestClient

from assistx import api as api_module


class _FakeNeo:
    def __init__(self, runs, tasks_by_status):
        self._runs = runs
        self._tasks_by_status = tasks_by_status

    def list_evaluation_runs(self, limit=100, status=None):
        return self._runs

    def get_tasks_by_status(self, status, limit=50):
        return self._tasks_by_status.get(status, [])


def _client(monkeypatch, neo):
    monkeypatch.setattr(api_module, "_neo", lambda: neo)
    monkeypatch.setitem(
        api_module.app.dependency_overrides, api_module.auth, lambda: "operator"
    )
    return TestClient(api_module.app)


def test_api_harness_evolution_serves_snapshot(monkeypatch):
    runs = [{
        "id": "run-1",
        "status": "DONE",
        "score": 1.0,
        "created_at_ts": 1234,
        "metadata_json": json.dumps({
            "harness_evolution": True,
            "chain_id": "hchain-abc",
            "stage": "rescore",
            "suite_id": "cpm-tb2-bench-v1",
            "endpoint": "optiplex:1235",
            "model_key": "minicpm5-2b-iter5",
        }),
    }]
    tasks = {
        "RUNNING": [{
            "id": "task-1",
            "kind": "harness_reflect",
            "title": "Harness reflect cpm-tb2-bench-v1 on optiplex:1235",
            "status": "RUNNING",
            "target_agent_id": "optiplex",
            "updated_at_ts": 2222,
            "payload_json": json.dumps({
                "harness_reflect": True,
                "endpoint": "optiplex:1235",
                "chain": {"chain_id": "hchain-abc", "stage": "reflect"},
                "fail_set": [{"task_id": "BM-007", "expected": "e", "actual": "a"}],
            }),
        }],
    }
    client = _client(monkeypatch, _FakeNeo(runs, tasks))
    res = client.get("/api/harness/evolution")
    assert res.status_code == 200
    body = res.json()
    assert body["chain_count"] == 1
    assert body["chains"][0]["chain_id"] == "hchain-abc"
    assert body["chains"][0]["stages"][0]["stage"] == "rescore"
    assert body["live_tasks"][0]["kind"] == "harness_reflect"
    assert body["mistakes"][0]["task_id"] == "BM-007"


def test_api_harness_evolution_degrades_neo4j_failure_to_empty(monkeypatch):
    class _BrokenNeo:
        def list_evaluation_runs(self, limit=100, status=None):
            raise RuntimeError("neo4j down")

        def get_tasks_by_status(self, status, limit=50):
            raise RuntimeError("neo4j down")

    client = _client(monkeypatch, _BrokenNeo())
    res = client.get("/api/harness/evolution")
    assert res.status_code == 200
    assert res.json()["chain_count"] == 0
    assert res.json()["live_tasks"] == []


def test_harness_page_renders(monkeypatch):
    client = _client(monkeypatch, _FakeNeo([], {}))
    res = client.get("/harness")
    assert res.status_code == 200
    text = res.text
    assert "Harness evolution" in text
    assert "css/control_room.css" in text
    assert "js/harness.js" in text
