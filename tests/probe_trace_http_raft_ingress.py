"""Manual-only FastAPI -> authenticated identity -> Unix writer -> real Raft.

Runs inside the disposable three-voter etcd RBAC acceptance, NOT a public
HTTP listener or a deployed AssistX mobile route. Graph.execute here is a
local stub; real Neo4j Bolt/witness acceptance is separately run in the same
three-voter orchestrator when its other explicit flag is enabled.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.testclient import TestClient

from trace_etcd_policy_writer_research import UnixPolicyGrantAdapter
from trace_graph_entry_guarded import ProtectedGraphEntry, QueryPlan
from trace_graph_http_ingress_research import (
    build_research_idempotent_graph_router
)

_SECURITY = HTTPBasic(auto_error=False)


def _test_verified_login(
    credentials: HTTPBasicCredentials | None = Depends(_SECURITY)
) -> str:
    # Research synthetic basic credentials; no production identity adapter.
    if credentials is None or not (
        secrets.compare_digest(credentials.username, "fixture-alice")
        and secrets.compare_digest(credentials.password, "fixture-pass-only")
    ):
        raise HTTPException(401, "test_authentication_denied")
    return credentials.username


def run_http_against_scoped_raft(unix_client, real_authority, service_key):
    adapter = UnixPolicyGrantAdapter(unix_client, "rbac-service-genesis")

    class Journal:
        events = []
        def append(self, kind, attempt, plan_id):
            self.events.append((kind, attempt.operation_id))

    class GraphStub:
        calls = 0
        def execute(self, attempt, cypher, parameters):
            self.calls += 1
            assert cypher == "RETURN $value AS value"
            return {"value": parameters["value"]}

    journal, graph = Journal(), GraphStub()
    entry = ProtectedGraphEntry(
        {"approved_read": QueryPlan("RETURN $value AS value", ("value",))},
        adapter, journal, graph, "rbac-service-genesis")
    app = FastAPI()
    app.include_router(
        build_research_idempotent_graph_router(
            entry, _test_verified_login, adapter))
    stable_key = "disposable-http-request-0001"

    def request(value, user="fixture-alice", password="fixture-pass-only"):
        return client.post(
            "/_research/graph/execute",
            json={"plan_id": "approved_read",
                  "parameters": {"value": value}},
            headers={"Idempotency-Key": stable_key,
                     "X-Verified-Subject": "trusted-operator",
                     "Tailscale-User-Login": "trusted-operator"},
            auth=(user, password),
        )
    with TestClient(app) as client:
        assert request(42, user="attacker", password="wrong").status_code == 401
        assert request(42).status_code == 200
        assert request(42).status_code == 409
        assert request(100).status_code == 409
        assert graph.calls == 1
        result = client.get(
            "/_research/graph/requests/" + stable_key,
            auth=("fixture-alice", "fixture-pass-only"))
        assert result.status_code == 200
        assert result.json() == {"state": "PENDING_EXECUTION_UNCERTAIN"}

    view = real_authority.snapshot()
    assert len(view.document["pending"]) == 1
    token = next(iter(view.document["pending"]))
    assert view.document["pending"][token]["request_digest"] is not None
    assert view.document["pending"][token]["operation"].startswith("auth:")
    assert len(journal.events) == 3
    assert not hasattr(unix_client, "txn")
    return token
