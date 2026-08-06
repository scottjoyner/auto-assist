from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistx.executor_security import build_executor_security_router


class FakeRow(dict):
    pass


class FakeResult:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def single(self) -> dict[str, Any] | None:
        return self.row


class FakeSession:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def run(self, query: str, parameters: dict[str, Any]) -> FakeResult:
        return FakeResult(self.row)


class FakeNeo:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row
        self.closed = False

    def _session(self) -> FakeSession:
        return FakeSession(self.row)

    def close(self) -> None:
        self.closed = True


class FakeFactory:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row
        self.instances: list[FakeNeo] = []

    def __call__(self) -> FakeNeo:
        neo = FakeNeo(self.row)
        self.instances.append(neo)
        return neo


def active_row(**task_overrides: Any) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    task = {
        "id": "task-1",
        "status": "RUNNING",
        "claim_id": "claim-1",
        "claimed_by": "fleet-executor",
        "lease_expires_at_ts": now_ms + 60_000,
    }
    task.update(task_overrides)
    return {
        "task": task,
        "projection_generation": 12,
        "projection_status": "approved",
        "projection_expires_at_ts": now_ms + 60_000,
    }


def client_for(factory: FakeFactory) -> TestClient:
    app = FastAPI()
    app.include_router(build_executor_security_router(factory))
    return TestClient(app)


def test_claim_status_matches_auto_router_contract(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTX_EXECUTOR_CLAIM_STATUS_TOKEN", "shared-secret")
    factory = FakeFactory(active_row())

    response = client_for(factory).get(
        "/api/executor/claims/task-1/status",
        headers={"Authorization": "Bearer shared-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "active": True,
        "reason": "active",
        "task_id": "task-1",
        "claim_id": "claim-1",
        "agent_id": "fleet-executor",
        "lease_expires_at_ts": response.json()["lease_expires_at_ts"],
        "projection_generation": 12,
        "projection_expires_at_ts": response.json()["projection_expires_at_ts"],
    }
    assert factory.instances[0].closed is True


def test_claim_status_fails_closed_for_expired_claim(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTX_EXECUTOR_CLAIM_STATUS_TOKEN", "shared-secret")
    factory = FakeFactory(
        active_row(lease_expires_at_ts=int(time.time() * 1000) - 1)
    )

    response = client_for(factory).get(
        "/api/executor/claims/task-1/status",
        headers={"Authorization": "Bearer shared-secret"},
    )

    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["reason"] == "claim_expired"


def test_claim_status_requires_dedicated_service_token(monkeypatch) -> None:
    monkeypatch.setenv("ASSISTX_EXECUTOR_CLAIM_STATUS_TOKEN", "shared-secret")
    factory = FakeFactory(active_row())

    response = client_for(factory).get(
        "/api/executor/claims/task-1/status",
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 401
    assert factory.instances == []
