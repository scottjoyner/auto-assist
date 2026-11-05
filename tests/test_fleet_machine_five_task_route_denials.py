"""All five legacy Task routes must reject a registered machine BEFORE graph access.

Real ASGI TestClient, synthetic headers only, no fleet I/O or production graph.
This is an identity-only staging regression: it MUST NOT enable Task work.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from assistx import api, api_router

NODE_ID = "node-synthetic"
NODE_TOKEN = "synthetic-node-secret"
BASIC = ("synthetic-operator", "synthetic-operator-pass")

TASK_ROUTES = (
    ("GET", "/api/agent/tasks?agent_id=node-synthetic&status=READY&limit=1", None),
    ("POST", "/api/tasks/synthetic-task/claim", {
        "agent_id": NODE_ID, "session_id": "synthetic-session",
        "capabilities": ["script"]
    }),
    ("POST", "/api/tasks/synthetic-task/heartbeat", {
        "agent_id": NODE_ID, "session_id": "synthetic-session",
        "claim_id": "synthetic-claim"
    }),
    ("POST", "/api/tasks/synthetic-task/complete", {
        "agent_id": NODE_ID, "session_id": "synthetic-session",
        "claim_id": "synthetic-claim", "status": "DONE",
        "result": {"synthetic": True}
    }),
    ("POST", "/api/tasks/synthetic-task/checkpoint", {
        "agent_id": NODE_ID, "claim_id": "synthetic-claim",
        "checkpoint": {}, "progress": 0.5
    }),
)


@pytest.fixture
def machine_only(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps({
        NODE_ID: NODE_TOKEN,
    }))
    monkeypatch.setenv("BASIC_AUTH_USER", BASIC[0])
    monkeypatch.setenv("BASIC_AUTH_PASS", BASIC[1])
    monkeypatch.setattr(api, "USER", BASIC[0])
    monkeypatch.setattr(api, "PASS", BASIC[1])

    def graph_must_not_be_reached():
        pytest.fail("machine-only staging MUST NOT open any graph connection")

    monkeypatch.setattr(api, "_neo", graph_must_not_be_reached)
    monkeypatch.setattr(api, "_neo_fleet", graph_must_not_be_reached)
    return TestClient(api_router.app)


@pytest.mark.parametrize("method,path,payload", TASK_ROUTES)
@pytest.mark.parametrize("with_operator_basic", (False, True))
def test_registered_machine_cannot_reach_any_legacy_task_effect(
    machine_only, method, path, payload, with_operator_basic
):
    headers = {
        "X-Fleet-Node-Id": NODE_ID,
        "X-Fleet-Node-Token": NODE_TOKEN,
    }
    kwargs = {"headers": headers}
    if payload is not None:
        kwargs["json"] = payload
    if with_operator_basic:
        kwargs["auth"] = BASIC
    response = machine_only.request(method, path, **kwargs)
    assert response.status_code == 403, (method, path, response.status_code)


@pytest.mark.parametrize("method,path,payload", TASK_ROUTES)
def test_wrong_machine_token_never_falls_back_to_valid_basic(
    machine_only, method, path, payload
):
    kwargs = {
        "headers": {
            "X-Fleet-Node-Id": NODE_ID,
            "X-Fleet-Node-Token": "wrong-synthetic-token",
        },
        "auth": BASIC,
    }
    if payload is not None:
        kwargs["json"] = payload
    response = machine_only.request(method, path, **kwargs)
    assert response.status_code == 403


def test_positive_machine_identity_is_still_explicit_nonadmission(machine_only):
    headers = {
        "X-Fleet-Node-Id": NODE_ID,
        "X-Fleet-Node-Token": NODE_TOKEN,
    }
    response = machine_only.get("/api/fleet-node/identity", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "machine_identity_verified": True,
        "node_id": NODE_ID,
        "task_admission": "HOLD",
        "legacy_task_authorized": False,
    }
    assert machine_only.get(
        "/api/agent/tasks?agent_id=node-synthetic", headers=headers
    ).status_code == 403


def test_operator_basic_alone_is_not_machine_identity(machine_only):
    response = machine_only.get("/api/fleet-node/identity", auth=BASIC)
    assert response.status_code == 403
