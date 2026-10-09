"""Contract for the actual FastAPI trace GET router, not production deployment.

All graph dependencies are replaced by in-process fixtures. No Neo4j, network,
provider API or user credentials are touched by this test module.
"""
from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx import swarm_routes


_PATHS = ("/api/traces", "/api/traces/fixture", "/api/traces/fixture/evidence")


def _basic(user: str, password: str) -> dict[str, str]:
    value = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": "Basic " + value}


def _client(monkeypatch, principal):
    created = []

    class FakeNeo:
        closed = False

        def close(self):
            self.closed = True

    def make_neo():
        neo = FakeNeo()
        created.append(neo)
        return neo

    monkeypatch.setattr(swarm_routes, "_injected_auth_dependency", principal)
    monkeypatch.setattr(swarm_routes, "_neo", make_neo)
    monkeypatch.setattr(swarm_routes, "list_traces", lambda neo, **kwargs: {
        "traces": [], "total": 0, "limit": kwargs["limit"], "outcome": kwargs["outcome"] or "all"
    })
    monkeypatch.setattr(swarm_routes, "get_trace", lambda neo, cid: {
        "correlation_id": cid, "events": [], "current_state": "synthetic"
    })
    monkeypatch.setattr(swarm_routes, "get_trace_task_evidence", lambda neo, cid: {
        "correlation_id": cid, "schema": "trace-task-evidence-v1", "tasks": [],
        "source_authenticated": False, "node_or_agent_verified": False,
        "graph_write_permitted": False,
        "trust": "unverified_correspondence_not_execution_attestation",
    })
    app = FastAPI()
    app.include_router(swarm_routes.router)
    return TestClient(app), created


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("headers", [{}, _basic("unverified", "not-a-secret")])
def test_unmounted_operator_auth_fails_closed_before_neo4j(monkeypatch, path, headers):
    client, created = _client(monkeypatch, None)
    response = client.get(path, headers=headers)
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Basic"
    assert created == []


@pytest.mark.parametrize("principal", [None, "", "  ", 0, True])
def test_injected_auth_returning_no_identity_is_rejected(monkeypatch, principal):
    client, created = _client(monkeypatch, lambda req, credentials: principal)
    response = client.get("/api/traces", headers=_basic("demo", "demo"))
    assert response.status_code == 401
    assert created == []


@pytest.mark.parametrize("path", _PATHS)
def test_installed_auth_rejects_absent_or_bad_credentials_before_neo4j(monkeypatch, path):
    def validate(request, credentials):
        if not credentials or (credentials.username, credentials.password) != ("fixture", "synthetic"):
            raise HTTPException(status_code=401, detail="Authentication required")
        return "fixture"

    client, created = _client(monkeypatch, validate)
    for headers in ({}, _basic("fixture", "incorrect"), _basic("unknown", "synthetic")):
        response = client.get(path, headers=headers)
        assert response.status_code == 401
        assert created == []

    response = client.get(path, headers=_basic("fixture", "synthetic"))
    assert response.status_code == 200, response.text[:300]
    assert len(created) == 1 and created[0].closed is True


@pytest.mark.parametrize("path", [
    "/api/traces/" + "a" * 129,
    "/api/traces/" + "a" * 129 + "/evidence",
    "/api/traces?limit=201",
    "/api/traces?search=" + "b" * 129,
])
def test_validated_trace_read_path_and_query_are_bounded_before_storage(monkeypatch, path):
    client, created = _client(monkeypatch, lambda req, credentials: "fixture")
    response = client.get(path, headers=_basic("fixture", "synthetic"))
    assert response.status_code == 422
    assert created == []


@pytest.mark.parametrize("status", [401, 403])
def test_operator_permission_denial_preserves_status_and_skips_storage(monkeypatch, status):
    def deny(request, credentials):
        raise HTTPException(status_code=status, detail="Operator permission denied")

    client, created = _client(monkeypatch, deny)
    for path in _PATHS:
        response = client.get(path, headers=_basic("fixture", "synthetic"))
        assert response.status_code == status
    assert created == []


@pytest.mark.parametrize("path", _PATHS)
def test_trace_budget_default_off_never_probes_redis(monkeypatch, path):
    from assistx import trace_read_budget

    monkeypatch.delenv("ASSISTX_TRACE_READ_BUDGET_MODE", raising=False)
    monkeypatch.setattr(swarm_routes, "check_trace_read_budget", lambda principal: (_ for _ in ()).throw(
        AssertionError("budget must be disabled unless explicitly enabled")
    ))
    client, created = _client(monkeypatch, lambda req, credentials: "fixture")
    response = client.get(path, headers=_basic("fixture", "synthetic"))
    assert response.status_code == 200
    assert len(created) == 1 and created[0].closed


@pytest.mark.parametrize("path", _PATHS)
def test_trace_budget_enforce_denies_before_storage_with_retry_after(monkeypatch, path):
    monkeypatch.setenv("ASSISTX_TRACE_READ_BUDGET_MODE", "enforce")
    checked = []

    def deny(principal):
        checked.append(principal)
        return (False, 0, 7)

    monkeypatch.setattr(swarm_routes, "check_trace_read_budget", deny)
    client, created = _client(monkeypatch, lambda req, credentials: "verified-operator")
    response = client.get(path, headers=_basic("fixture", "synthetic"))
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "7"
    assert checked == ["verified-operator"]
    assert created == []


@pytest.mark.parametrize("path", _PATHS)
def test_trace_budget_redis_outage_fails_closed_before_storage(monkeypatch, path):
    from assistx.trace_read_budget import TraceReadBudgetUnavailable

    monkeypatch.setenv("ASSISTX_TRACE_READ_BUDGET_MODE", "enforce")

    def unavailable(principal):
        raise TraceReadBudgetUnavailable("synthetic error")

    monkeypatch.setattr(swarm_routes, "check_trace_read_budget", unavailable)
    client, created = _client(monkeypatch, lambda req, credentials: "verified-operator")
    response = client.get(path, headers=_basic("fixture", "synthetic"))
    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "5"
    assert created == []


def test_trace_budget_authentication_precedes_quota_and_never_reads_storage(monkeypatch):
    monkeypatch.setenv("ASSISTX_TRACE_READ_BUDGET_MODE", "enforce")

    def reject(request, credentials):
        raise HTTPException(status_code=401, detail="Denied")

    monkeypatch.setattr(swarm_routes, "check_trace_read_budget", lambda principal: (_ for _ in ()).throw(
        AssertionError("quota must not run before authorization")
    ))
    client, created = _client(monkeypatch, reject)
    for path in _PATHS:
        response = client.get(path, headers={})
        assert response.status_code == 401
    assert created == []


def test_trace_budget_typo_in_mode_never_silently_falls_back_to_unmetered(monkeypatch):
    monkeypatch.setenv("ASSISTX_TRACE_READ_BUDGET_MODE", "enforced")
    client, created = _client(monkeypatch, lambda req, credentials: "verified-operator")
    response = client.get("/api/traces", headers=_basic("fixture", "synthetic"))
    assert response.status_code == 503
    assert created == []


def test_trace_budget_allows_operator_with_positive_atomic_admission(monkeypatch):
    monkeypatch.setenv("ASSISTX_TRACE_READ_BUDGET_MODE", "enforce")
    monkeypatch.setattr(swarm_routes, "check_trace_read_budget", lambda principal: (True, 22, 0))
    client, created = _client(monkeypatch, lambda req, credentials: "verified-operator")
    response = client.get("/api/traces/fixture", headers=_basic("fixture", "synthetic"))
    assert response.status_code == 200
    assert len(created) == 1 and created[0].closed
