"""Exact deployed-base HTTP contract for the additive identity-only route.

All node tokens and operator credentials are synthetic. No graph or network.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from assistx import api, api_router

ID = "xwing"
TOKEN = "synthetic-not-a-real-machine-token"
BASIC = ("synthetic-operator", "synthetic-operator-password")
MACHINE = {"X-Fleet-Node-Id": ID, "X-Fleet-Node-Token": TOKEN}
ROUTES = (
    ("GET", "/api/agent/tasks?agent_id=xwing&status=READY", None),
    ("POST", "/api/tasks/synthetic/claim", {"agent_id": ID}),
    ("POST", "/api/tasks/synthetic/heartbeat", {"agent_id": ID}),
    ("POST", "/api/tasks/synthetic/complete", {"agent_id": ID}),
    ("POST", "/api/tasks/synthetic/checkpoint", {"agent_id": ID}),
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", json.dumps({ID: TOKEN}))
    monkeypatch.setenv("BASIC_AUTH_USER", BASIC[0])
    monkeypatch.setenv("BASIC_AUTH_PASS", BASIC[1])
    monkeypatch.setattr(api, "USER", BASIC[0])
    monkeypatch.setattr(api, "PASS", BASIC[1])
    monkeypatch.setattr(api, "_neo", lambda: pytest.fail("unexpected graph read"))
    monkeypatch.setattr(api, "_neo_fleet", lambda: pytest.fail("unexpected graph read"))
    return TestClient(api_router.app)


def test_identity_only_shape_no_task_authority(client):
    response = client.get("/api/fleet-node/identity", headers=MACHINE)
    assert response.status_code == 200
    assert response.json() == {
        "machine_identity_verified": True, "node_id": ID,
        "task_admission": "HOLD", "legacy_task_authorized": False,
    }


def test_basic_alone_is_not_machine_identity(client):
    assert client.get("/api/fleet-node/identity", auth=BASIC).status_code == 403


@pytest.mark.parametrize("extra", (
    {"X-Fleet-Node-Id": ID}, {"X-Fleet-Node-Token": TOKEN},
    {"X-Fleet-Node-Id": "other", "X-Fleet-Node-Token": TOKEN},
    {"X-Fleet-Node-Id": ID, "X-Fleet-Node-Token": "wrong"},
))
def test_partial_wrong_machine_never_falls_back_to_basic(client, extra):
    assert client.get("/api/fleet-node/identity", headers=extra,
                      auth=BASIC).status_code == 403


@pytest.mark.parametrize("method,path,body", ROUTES)
@pytest.mark.parametrize("with_basic", [False, True])
def test_any_machine_header_blocks_legacy_task_before_graph(
    client, method, path, body, with_basic
):
    kwargs = {"headers": MACHINE}
    if body is not None:
        kwargs["json"] = body
    if with_basic:
        kwargs["auth"] = BASIC
    assert client.request(method, path, **kwargs).status_code == 403


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_partial_machine_header_also_blocks_legacy_task(client, method, path, body):
    kwargs = {"headers": {"X-Fleet-Node-Id": ID}, "auth": BASIC}
    if body is not None:
        kwargs["json"] = body
    assert client.request(method, path, **kwargs).status_code == 403


@pytest.mark.parametrize("malformed", (
    "", "not json", "[]", '{"xwing": false}',
    '{"xwing": "wrong", "xwing": "synthetic-not-a-real-machine-token"}',
))
def test_registry_malformed_duplicate_or_nonstring_never_authenticates(
    client, monkeypatch, malformed
):
    monkeypatch.setenv("ASSISTX_FLEET_NODE_TOKENS", malformed)
    assert client.get("/api/fleet-node/identity", headers=MACHINE).status_code == 403


def test_identity_route_exposed_by_complete_asgi_openapi():
    # The test harness may wrap routes under a private included router. The
    # full ASGI schema, not the outer router's immediate child list, is the
    # authoritative registration witness.
    assert "/api/fleet-node/identity" in api_router.app.openapi()["paths"]
