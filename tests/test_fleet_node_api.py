"""API-level regression tests for the fleet-node-agent identity path.

Requires the full application dependency set (langgraph & co). When those are
unavailable the module is skipped; the lightweight identity checks in
``tests/test_fleet_node_auth.py`` still run. Synthetic fixtures only.
"""
from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="full app dependencies not installed")
api_router = pytest.importorskip(
    "assistx.api_router", reason="full application dependencies not installed"
)
TestClient = pytest.importorskip("fastapi.testclient").TestClient

_SYNTHETIC_TOKENS = {"node-a": "synthetic-token-a", "node-b": "synthetic-token-b"}


@pytest.fixture
def node_tokens(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    monkeypatch.setenv("FLEET_NODE_TOKEN", _SYNTHETIC_TOKENS["node-a"])
    monkeypatch.setenv("FLEET_NODE_ID", "node-a")


@pytest.fixture
def fake_fleet_neo(monkeypatch):
    class FakeNeo:
        def __init__(self):
            self.calls: list[str] = []

        def close(self):
            return None

        def list_agent_tasks(self, **_kwargs):
            self.calls.append("list_agent_tasks")
            return []

        def get_task(self, _task_id):
            self.calls.append("get_task")
            return None

    fake = FakeNeo()
    monkeypatch.setattr("assistx.api._neo_fleet", lambda: fake)
    monkeypatch.setattr(
        "assistx.api._workflow_runtime_snapshot",
        lambda _neo: {"running": 0, "interactive_ready": 0, "critical_ready": 0},
    )
    return fake


def test_agent_tasks_401_without_credentials(fake_fleet_neo):
    client = TestClient(api_router.app)
    r = client.get("/api/agent/tasks")
    assert r.status_code == 401
    assert fake_fleet_neo.calls == []


def test_agent_tasks_403_with_unregistered_node_identity(monkeypatch, fake_fleet_neo):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    client = TestClient(api_router.app)
    r = client.get(
        "/api/agent/tasks",
        headers={"X-Fleet-Node-Id": "intruder", "X-Fleet-Node-Token": "synthetic-token-a"},
    )
    assert r.status_code == 403
    r = client.get(
        "/api/agent/tasks",
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": "wrong-token"},
    )
    assert r.status_code == 403
    assert fake_fleet_neo.calls == []


def test_agent_tasks_node_token_wrong_agent_id_rejected(
    monkeypatch, node_tokens, fake_fleet_neo
):
    client = TestClient(api_router.app)
    r = client.get(
        "/api/agent/tasks?agent_id=node-b",
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": _SYNTHETIC_TOKENS["node-a"]},
    )
    assert r.status_code == 403
    assert fake_fleet_neo.calls == []


def test_agent_tasks_200_with_registered_node_identity(
    monkeypatch, node_tokens, fake_fleet_neo
):
    client = TestClient(api_router.app)
    r = client.get(
        "/api/agent/tasks?agent_id=node-a",
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": _SYNTHETIC_TOKENS["node-a"]},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_agent_tasks_200_with_basic_auth_still_accepted(monkeypatch, fake_fleet_neo):
    monkeypatch.setenv("BASIC_AUTH_USER", "operator")
    monkeypatch.setenv("BASIC_AUTH_PASS", "synthetic-operator-pass")
    import assistx.api as api

    monkeypatch.setattr(api, "USER", "operator")
    monkeypatch.setattr(api, "PASS", "synthetic-operator-pass")
    client = TestClient(api_router.app)
    r = client.get("/api/agent/tasks", auth=("operator", "synthetic-operator-pass"))
    assert r.status_code == 200


def test_claim_rejects_node_identity_mismatch_before_graph(
    monkeypatch, node_tokens, fake_fleet_neo
):
    client = TestClient(api_router.app)
    r = client.post(
        "/api/tasks/task-synthetic/claim",
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": _SYNTHETIC_TOKENS["node-a"]},
        json={"agent_id": "node-b", "capabilities": ["script"], "session_id": "s"},
    )
    assert r.status_code == 403
    assert fake_fleet_neo.calls == []


def test_claim_rejects_invalid_node_token_before_graph(
    monkeypatch, node_tokens, fake_fleet_neo
):
    client = TestClient(api_router.app)
    r = client.post(
        "/api/tasks/task-synthetic/claim",
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": "intruder-token"},
        json={"agent_id": "node-a", "capabilities": ["script"], "session_id": "s"},
    )
    assert r.status_code == 403
    assert fake_fleet_neo.calls == []


@pytest.mark.parametrize("headers", [
    {"X-Fleet-Node-Id": "node-a"},
    {"X-Fleet-Node-Token": "synthetic-token-a"},
    {"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": "wrong-token"},
])
def test_invalid_node_headers_never_fall_back_to_valid_basic(
    monkeypatch, fake_fleet_neo, headers
):
    """A valid operator password must not rescue a malformed node request."""
    import assistx.api as api

    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps(_SYNTHETIC_TOKENS))
    monkeypatch.setattr(api, "USER", "synthetic-operator")
    monkeypatch.setattr(api, "PASS", "synthetic-operator-secret")
    client = TestClient(api_router.app)
    response = client.get(
        "/api/agent/tasks", headers=headers,
        auth=("synthetic-operator", "synthetic-operator-secret"),
    )
    assert response.status_code == 403
    assert fake_fleet_neo.calls == []


@pytest.mark.parametrize("endpoint,body", [
    ("claim", {"agent_id": "node-b", "capabilities": ["script"]}),
    ("heartbeat", {"agent_id": "node-b", "status": "RUNNING"}),
    ("complete", {"agent_id": "node-b", "status": "DONE"}),
    ("checkpoint", {"agent_id": "node-b", "claim_id": "synthetic-claim"}),
])
def test_mutation_routes_reject_cross_node_identity_before_graph(
    monkeypatch, node_tokens, fake_fleet_neo, endpoint, body
):
    """One node's credential must not mutate another node's tasks."""
    import assistx.api as api

    def forbidden_neo():
        pytest.fail("cross-node mutation reached graph initialization")

    monkeypatch.setattr(api, "_neo", forbidden_neo)
    monkeypatch.setattr(api, "_neo_fleet", forbidden_neo)
    client = TestClient(api_router.app)
    response = client.post(
        f"/api/tasks/task-synthetic/{endpoint}",
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": _SYNTHETIC_TOKENS["node-a"]},
        json=body,
    )
    assert response.status_code == 403
    assert fake_fleet_neo.calls == []
