from __future__ import annotations

import os

from fastapi.testclient import TestClient

from assistx.continuity_api import app, set_store_for_testing
from assistx.continuity_state import ContinuityConfig, InMemoryContinuityStore

TOKEN = "continuity-api-token-123456"


def client():
    os.environ["ASSISTX_CONTINUITY_API_TOKEN"] = TOKEN
    store = InMemoryContinuityStore(
        ContinuityConfig("fleet", "beelink", "continuity-secret-123456")
    )
    set_store_for_testing(store)
    return TestClient(app), store


def auth():
    return {"X-Continuity-Token": TOKEN}


def test_health_is_public_but_status_requires_token():
    api, _store = client()
    assert api.get("/health").status_code == 200
    assert api.get("/v1/continuity/status").status_code == 401
    assert api.get("/v1/continuity/status", headers=auth()).status_code == 200


def test_epoch_task_and_durable_outbox_lifecycle():
    api, _store = client()
    response = api.post(
        "/v1/continuity/epoch/advance",
        headers=auth(),
        json={"epoch": 3, "fence_proof": "witness:epoch-3"},
    )
    assert response.status_code == 200

    heartbeat = api.post(
        "/v1/continuity/heartbeat",
        headers=auth(),
        json={
            "node_id": "xwing",
            "capabilities": ["code", "lmstudio"],
            "status": "healthy",
            "max_slots": 2,
            "memory_available_mb": 6000,
        },
    )
    heartbeat.raise_for_status()

    task = api.post(
        "/v1/continuity/tasks",
        headers=auth(),
        json={
            "task_id": "t1",
            "title": "Repair router",
            "epoch": 3,
            "required_capabilities": ["code"],
        },
    )
    assert task.status_code == 200

    claim = api.post(
        "/v1/continuity/tasks/claim",
        headers=auth(),
        json={"node_id": "xwing", "capabilities": ["code"], "epoch": 3},
    ).json()["task"]
    completed = api.post(
        "/v1/continuity/tasks/t1/complete",
        headers=auth(),
        json={
            "node_id": "xwing",
            "claim_token": claim["claim_token"],
            "status": "completed",
            "result": {"commit": "abc"},
        },
    )
    assert completed.status_code == 200
    outbox = api.get("/v1/continuity/outbox", headers=auth()).json()
    assert outbox["count"] == 2


def test_router_projection_fails_closed_when_missing_and_serves_fresh_document():
    api, _store = client()
    assert api.get("/api/router/runtime-projection", headers=auth()).status_code == 503
    stored = api.put(
        "/v1/continuity/documents/runtime-projection",
        headers=auth(),
        json={
            "epoch": 0,
            "ttl_ms": 60_000,
            "payload": {"generation": 7, "providers": []},
        },
    )
    stored.raise_for_status()
    response = api.get("/api/router/runtime-projection", headers=auth())
    assert response.status_code == 200
    assert response.json()["generation"] == 7
