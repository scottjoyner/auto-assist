"""Isolated, read-only API route-registration smoke. NOT a production startup test.

Only mounts the lease router on an in-process FastAPI fixture. It never imports
the production api.py module (whose lifespan can initialize Neo4j/workers).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from assistx.trace_claim_lease_api import build_claim_lease_router

API_SOURCE = Path(__file__).resolve().parents[1] / "src/assistx/api.py"


def _test_client(*, authorized=True):
    observed = {"neo_calls": 0, "node_verifications": 0}
    def neo_factory():
        observed["neo_calls"] += 1
        raise AssertionError("Neo4j must never be accessed by disabled router")
    def auth_dependency():
        if not authorized:
            raise HTTPException(status_code=401, detail="fixture_not_authenticated")
        return "fixture-user"
    def verify_node_identity(node_id, token):
        observed["node_verifications"] += 1
        if node_id != "xwing" or token != "test-only-fixture-token":
            raise HTTPException(status_code=403, detail="node_denied")
    app = FastAPI()
    app.include_router(build_claim_lease_router(
        neo_factory=neo_factory,
        auth_dependency=auth_dependency,
        verify_node_identity=verify_node_identity,
    ))
    return TestClient(app), observed


CLAIM = {
    "node_id": "xwing", "task_id": "synthetic-no-claim", "claim_id": "synthetic-only",
    "execution_attempt": 1,
}
STATUS = {
    "lease_proof": {"node_id": "xwing", "task_id": "synthetic-no-claim"},
    "challenge": "b" * 64,
}
ENDPOINTS = (
    ("/api/fleet/trace-execution/claim-lease-proof", CLAIM),
    ("/api/fleet/trace-execution/claim-current-status", STATUS),
)


@pytest.mark.parametrize("url,payload", ENDPOINTS)
def test_disabled_router_returns_503_before_node_check_or_neo(monkeypatch, url, payload):
    monkeypatch.delenv("ASSISTX_TRACE_LEASE_ISSUER_ENABLED", raising=False)
    client, observed = _test_client()
    response = client.post(url, json=payload, headers={"x-fleet-node-token": "test-only-fixture-token"})
    assert response.status_code == 503
    assert response.json()["detail"] == "trace_lease_issuer_disabled"
    assert observed == {"neo_calls": 0, "node_verifications": 0}


@pytest.mark.parametrize("url,payload", ENDPOINTS)
def test_disabled_router_does_not_bypass_auth(monkeypatch, url, payload):
    monkeypatch.setenv("ASSISTX_TRACE_LEASE_ISSUER_ENABLED", "false")
    client, observed = _test_client(authorized=False)
    response = client.post(url, json=payload)
    assert response.status_code == 401
    assert observed == {"neo_calls": 0, "node_verifications": 0}


@pytest.mark.parametrize("url,payload", ENDPOINTS)
def test_enabled_fixture_without_signer_fails_closed_before_graph(monkeypatch, url, payload):
    monkeypatch.setenv("ASSISTX_TRACE_LEASE_ISSUER_ENABLED", "true")
    monkeypatch.delenv("ASSISTX_TRACE_LEASE_SIGNING_KEY_FILE", raising=False)
    client, observed = _test_client()
    response = client.post(url, json=payload, headers={"x-fleet-node-token": "test-only-fixture-token"})
    assert response.status_code == 503
    assert response.json()["detail"] == "claim_signing_key_not_configured"
    assert observed["neo_calls"] == 0


@pytest.mark.parametrize("url,payload", ENDPOINTS)
def test_wrong_node_identity_rejected_before_signer_or_graph(monkeypatch, url, payload):
    monkeypatch.setenv("ASSISTX_TRACE_LEASE_ISSUER_ENABLED", "true")
    client, observed = _test_client()
    response = client.post(url, json=payload, headers={"x-fleet-node-token": "wrong-fixture-token"})
    assert response.status_code == 403
    assert observed["neo_calls"] == 0
    assert observed["node_verifications"] == 1


def test_production_source_declares_real_router_registration_without_importing_api():
    tree = ast.parse(API_SOURCE.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "include_router" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id != "app":
            continue
        if any(
            isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
            and arg.func.id == "build_claim_lease_router"
            for arg in node.args
        ):
            found = True
    assert found, "production source lost explicit disabled trace router registration"
