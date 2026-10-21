from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.control_room_recovery import build_recovery_router


def _client() -> TestClient:
    app = FastAPI()

    def _auth() -> str:
        return "operator"

    app.include_router(build_recovery_router(_auth))
    return TestClient(app)


def test_island_heartbeat_route_is_registered_and_token_gated(monkeypatch) -> None:
    """The panel's island feed must actually exist on the wire.

    control_room.py renders island_recovery_snapshot() into the control room, but
    for a long time the ingest route was defined in control_room_recovery.py and
    never included in the application, so every heartbeat from beelink's
    island-heartbeat.timer 404'd and the panel rendered STALE. This test fails if
    the route is dropped or unregistered again.
    """
    monkeypatch.setenv("CONTROL_ROOM_ISLAND_TOKEN", "unit-test-token")
    client = _client()

    paths = {route.path for route in client.app.routes}
    assert "/api/control-room/island-heartbeat" in paths
    assert "/api/control-room/recovery" in paths

    # No token -> refused.
    refused = client.post("/api/control-room/island-heartbeat", json={"mode": "DEGRADED"})
    assert refused.status_code == 401

    # Wrong token -> refused.
    wrong = client.post(
        "/api/control-room/island-heartbeat",
        json={"mode": "DEGRADED"},
        headers={"X-Island-Token": "not-the-token"},
    )
    assert wrong.status_code == 401

    # Correct token -> accepted, and the panel feed reflects it.
    ok = client.post(
        "/api/control-room/island-heartbeat",
        json={"mode": "DEGRADED", "journal_pending": 7},
        headers={"X-Island-Token": "unit-test-token"},
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    snapshot = client.get("/api/control-room/recovery")
    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["present"] is True
    assert body["stale"] is False
    assert body["payload"]["mode"] == "DEGRADED"


def test_island_heartbeat_rejects_oversized_payload(monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_ROOM_ISLAND_TOKEN", "unit-test-token")
    client = _client()
    oversized = client.post(
        "/api/control-room/island-heartbeat",
        json={"blob": "x" * 70_000},
        headers={"X-Island-Token": "unit-test-token"},
    )
    assert oversized.status_code == 413


def test_island_feed_is_stale_after_the_window(monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_ROOM_ISLAND_TOKEN", "unit-test-token")
    client = _client()
    client.post(
        "/api/control-room/island-heartbeat",
        json={"mode": "DEGRADED"},
        headers={"X-Island-Token": "unit-test-token"},
    )
    # Move the recorded heartbeat out of the 5 minute window.
    import assistx.control_room_recovery as module

    module._RECEIVED_AT_MS = module._RECEIVED_AT_MS - (6 * 60 * 1000)
    body = client.get("/api/control-room/recovery").json()
    assert body["stale"] is True
    assert os.getenv("CONTROL_ROOM_ISLAND_TOKEN") == "unit-test-token"
