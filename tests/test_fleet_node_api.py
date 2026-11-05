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


def test_agent_tasks_403_with_registered_node_without_scoped_task_authority(
    monkeypatch, node_tokens, fake_fleet_neo
):
    client = TestClient(api_router.app)
    r = client.get(
        "/api/agent/tasks?agent_id=node-a",
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": _SYNTHETIC_TOKENS["node-a"]},
    )
    assert r.status_code == 403
    assert fake_fleet_neo.calls == []


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


def test_claim_same_registered_node_still_denied_before_graph(
    monkeypatch, node_tokens, fake_fleet_neo
):
    """Token identity cannot authorize a legacy mutable Task API."""
    client = TestClient(api_router.app)
    r = client.post(
        "/api/tasks/task-synthetic/claim",
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": _SYNTHETIC_TOKENS["node-a"]},
        json={"agent_id": "node-a", "capabilities": ["script"], "session_id": "s"},
    )
    assert r.status_code == 403
    assert fake_fleet_neo.calls == []


def test_verified_node_token_cannot_bypass_legacy_guard_with_basic(
    monkeypatch, node_tokens, fake_fleet_neo
):
    """Node headers win: adding a valid operator Basic must not bypass the denial."""
    monkeypatch.setenv("BASIC_AUTH_USER", "operator")
    monkeypatch.setenv("BASIC_AUTH_PASS", "synthetic-operator-pass")
    import assistx.api as api

    monkeypatch.setattr(api, "USER", "operator")
    monkeypatch.setattr(api, "PASS", "synthetic-operator-pass")
    client = TestClient(api_router.app)
    r = client.get(
        "/api/agent/tasks?agent_id=node-a",
        auth=("operator", "synthetic-operator-pass"),
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": _SYNTHETIC_TOKENS["node-a"]},
    )
    assert r.status_code == 403
    assert fake_fleet_neo.calls == []


def test_identity_probe_registered_node_without_task_permission(
    monkeypatch, node_tokens, fake_fleet_neo
):
    """Positive identity probe must not become legacy Task admission."""
    client = TestClient(api_router.app)
    headers = {
        "X-Fleet-Node-Id": "node-a",
        "X-Fleet-Node-Token": _SYNTHETIC_TOKENS["node-a"],
    }
    r = client.get("/api/fleet-node/identity", headers=headers)
    assert r.status_code == 200
    assert r.json() == {
        "machine_identity_verified": True,
        "node_id": "node-a",
        "task_admission": "HOLD",
        "legacy_task_authorized": False,
    }
    forbidden = client.get("/api/agent/tasks?agent_id=node-a", headers=headers)
    assert forbidden.status_code == 403
    assert fake_fleet_neo.calls == []


def test_identity_probe_denies_operator_only_and_invalid_machine(
    monkeypatch, node_tokens, fake_fleet_neo
):
    import assistx.api as api

    monkeypatch.setenv("BASIC_AUTH_USER", "operator")
    monkeypatch.setenv("BASIC_AUTH_PASS", "synthetic-operator-pass")
    monkeypatch.setattr(api, "USER", "operator")
    monkeypatch.setattr(api, "PASS", "synthetic-operator-pass")
    client = TestClient(api_router.app)
    r = client.get("/api/fleet-node/identity", auth=("operator", "synthetic-operator-pass"))
    assert r.status_code == 403
    invalid = client.get(
        "/api/fleet-node/identity",
        auth=("operator", "synthetic-operator-pass"),
        headers={"X-Fleet-Node-Id": "node-a", "X-Fleet-Node-Token": "wrong"},
    )
    assert invalid.status_code == 403
    assert fake_fleet_neo.calls == []


def test_spoofed_trusted_header_cannot_open_trace_ui_without_basic(
    monkeypatch, fake_fleet_neo
):
    """P0 #149: previously header-only /traces returned 200 in a synthetic lab."""
    import assistx.api as api

    monkeypatch.setattr(api, "TRUSTED_AUTH_HEADER", "X-Synthetic-Proxy-Identity")
    monkeypatch.setattr(api, "USER", "operator")
    monkeypatch.setattr(api, "PASS", "synthetic-operator-pass")
    client = TestClient(api_router.app)
    forged = client.get(
        "/traces", headers={"X-Synthetic-Proxy-Identity": "operator"}
    )
    assert forged.status_code == 401
    task = client.get(
        "/api/agent/tasks?agent_id=node-a",
        headers={"X-Synthetic-Proxy-Identity": "operator"},
    )
    assert task.status_code == 401
    assert fake_fleet_neo.calls == []


def test_spoofed_trusted_header_does_not_override_basic_identity(
    monkeypatch, fake_fleet_neo
):
    import assistx.api as api

    monkeypatch.setattr(api, "TRUSTED_AUTH_HEADER", "X-Synthetic-Proxy-Identity")
    monkeypatch.setattr(api, "USER", "operator")
    monkeypatch.setattr(api, "PASS", "synthetic-operator-pass")
    client = TestClient(api_router.app)
    accepted = client.get(
        "/api/agent/tasks?agent_id=node-a",
        auth=("operator", "synthetic-operator-pass"),
        headers={"X-Synthetic-Proxy-Identity": "forged-admin"},
    )
    assert accepted.status_code == 200
    wrong_basic = client.get(
        "/api/agent/tasks?agent_id=node-a",
        auth=("operator", "wrong-basic"),
        headers={"X-Synthetic-Proxy-Identity": "operator"},
    )
    assert wrong_basic.status_code == 401


def test_header_only_without_basic_configuration_is_not_authority(
    monkeypatch, fake_fleet_neo
):
    import assistx.api as api

    monkeypatch.setattr(api, "TRUSTED_AUTH_HEADER", "X-Synthetic-Proxy-Identity")
    monkeypatch.setattr(api, "USER", None)
    monkeypatch.setattr(api, "PASS", None)
    client = TestClient(api_router.app)
    response = client.get(
        "/api/agent/tasks",
        headers={"X-Synthetic-Proxy-Identity": "operator"},
    )
    assert response.status_code == 401
    assert fake_fleet_neo.calls == []
