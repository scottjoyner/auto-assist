"""FastAPI TestClient exercises authenticated HTTP → Raft CAS admission.

Research-only endpoint, no production route; exact identity is from verified
HTTP Basic in this test, not any supplied Tailscale-* or X-User header.
"""
from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from test_trace_etcd_quorum_fence_research import FakeEtcd
from test_trace_admission_retry_reconciliation import CommitThenDrop
from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, FenceRefused, canon, witness_receipt
)
from trace_graph_entry_guarded import ProtectedGraphEntry, QueryPlan
from trace_etcd_graph_admission_research import QuorumPlanGrantAdapter
from trace_graph_http_ingress_research import (
    build_research_idempotent_graph_router
)
from trace_graph_stable_identity_research import derive_stable_operation

EPOCH = "verified-http-research-fixture"
security = HTTPBasic(auto_error=False)


def verified_basic(credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    # Real credential verification against dummy TEST credentials only.
    if not credentials:
        raise HTTPException(401, "authentication_required")
    who, password = credentials.username, credentials.password
    valid_user = any(secrets.compare_digest(who, name)
                     for name in ("alice", "bob"))
    if not valid_user or not secrets.compare_digest(password, "test-password-only"):
        raise HTTPException(401, "authentication_failed")
    return who


class Graph:
    def __init__(self):
        self.calls = []
    def execute(self, attempt, cypher, parameters):
        self.calls.append((attempt, cypher, dict(parameters)))
        return {"answer": parameters["value"]}


class Journal:
    def __init__(self):
        self.events = []
    def append(self, kind, attempt, plan_id):
        self.events.append((kind, attempt.operation_id, plan_id))


def rig(store=None, capacity=2):
    store = store or FakeEtcd()
    operator = Ed25519PrivateKey.generate()
    witness = Ed25519PrivateKey.generate()
    public = lambda signer: signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    authority = EtcdQuorumFence(
        store, "/assistx/research/fencing/http-test")
    authority.bootstrap(EPOCH, "ingress-gateway", public(operator),
                        public(witness), capacity)
    entry_graph, journal = Graph(), Journal()
    adapter = QuorumPlanGrantAdapter(
        EtcdQuorumFence(store, authority.key, authority.pinned_cluster_id),
        "ingress-gateway", 1, EPOCH)
    entry = ProtectedGraphEntry(
        {"approved_read": QueryPlan("RETURN $value AS value", ("value",))},
        adapter, journal, entry_graph, EPOCH
    )
    app = FastAPI()
    app.include_router(
        build_research_idempotent_graph_router(entry, verified_basic, adapter))
    return TestClient(app), authority, store, witness, entry_graph, journal


def request(client, identity="alice", key="stable-request-0001", value=42,
            plan="approved_read", password="test-password-only", headers=None):
    supplied = {"Idempotency-Key": key}
    supplied.update(headers or {})
    return client.post(
        "/_research/graph/execute",
        json={"plan_id": plan, "parameters": {"value": value}},
        headers=supplied,
        auth=(identity, password)
    )


def test_authenticated_ingress_retries_are_409_without_second_bolt():
    client, quorum, store, witness, graph, journal = rig()
    first = request(client)
    assert first.status_code == 200, first.text
    assert first.json() == {"result": {"answer": 42}}
    assert request(client).status_code == 409
    assert request(client, value=43).status_code == 409
    assert request(client, plan="unknown").status_code == 422
    assert len(graph.calls) == 1
    assert len(quorum.snapshot().document["pending"]) == 1
    assert graph.calls[0][0].operation_id.startswith("auth:")
    assert graph.calls[0][0].operation_id not in str(first.json())


def test_same_client_key_is_independent_across_verified_principals():
    client, quorum, store, witness, graph, journal = rig()
    assert request(client, identity="alice").status_code == 200
    assert request(client, identity="bob").status_code == 200
    assert len(graph.calls) == 2
    assert graph.calls[0][0].operation_id != graph.calls[1][0].operation_id
    assert len(quorum.snapshot().document["pending"]) == 2


def test_spoofed_headers_do_not_override_verified_basic_identity():
    client, quorum, store, witness, graph, journal = rig()
    fake = {"X-Verified-Subject": "alice", "Tailscale-User-Login": "alice",
            "X-Forwarded-User": "alice"}
    assert request(client, identity="mallory", headers=fake).status_code == 401
    assert request(client, identity="alice", password="wrong",
                   headers=fake).status_code == 401
    assert not graph.calls
    assert not quorum.snapshot().document["pending"]


def test_missing_or_invalid_request_id_never_admits():
    client, quorum, store, witness, graph, journal = rig()
    response = client.post(
        "/_research/graph/execute",
        json={"plan_id": "approved_read", "parameters": {"value": 1}},
        auth=("alice", "test-password-only")
    )
    assert response.status_code == 428
    for bad in ("x", " " * 20, "x" * 129):
        assert request(client, key=bad).status_code == 422
    assert not graph.calls


def test_after_exact_witness_close_http_replay_cannot_execute():
    client, quorum, store, signer, graph, journal = rig()
    assert request(client).status_code == 200
    token = next(iter(quorum.snapshot().document["pending"]))
    quorum.bind(token, "ingress-gateway", 1,
                "disposable-neo4j", "instance-gen-1",
                "neo4j-transaction-100")
    receipt = witness_receipt(quorum.snapshot(), token)
    quorum.close_with_witness(token, receipt, signer.sign(canon(receipt)))
    assert not quorum.snapshot().document["pending"]
    assert request(client).status_code == 409
    assert request(client, value=100).status_code == 409
    assert len(graph.calls) == 1


def test_quorum_outage_prevents_graph_entry_and_emits_503():
    client, quorum, store, signer, graph, journal = rig()
    store.fail = True
    response = request(client)
    assert response.status_code == 503
    assert response.json()["detail"] == "execution_state_uncertain"
    assert not graph.calls


def test_successful_raft_cas_lost_ack_is_503_then_409_without_graph():
    store = CommitThenDrop()
    client, quorum, _, signer, graph, journal = rig(store)
    store.drop_next_success = True
    first = request(client)
    assert first.status_code == 503
    assert request(client).status_code == 409
    assert not graph.calls
    assert len(quorum.snapshot().document["pending"]) == 1


def test_parallel_http_requests_same_identity_never_double_execute():
    client, quorum, store, signer, graph, journal = rig(capacity=2)
    # Concurrent TestClient requests exercise real ingress/dependency,
    # not actual network clients/production load.
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(lambda _: request(client).status_code, range(12)))
    assert codes.count(200) == 1, codes
    assert codes.count(409) == 11, codes
    assert len(graph.calls) == 1
    assert len(quorum.snapshot().document["pending"]) == 1


def test_http_ingress_stable_id_passes_through_actual_unix_policy_writer(tmp_path):
    from test_trace_etcd_policy_writer_research import socket_rig, stop, GENESIS
    from trace_etcd_policy_writer_research import UnixPolicyGrantAdapter

    server, thread, unix_client, authority, witness, operator = socket_rig(tmp_path)
    graph, journal = Graph(), Journal()
    entry = ProtectedGraphEntry(
        {"approved_read": QueryPlan("RETURN $value AS value", ("value",))},
        UnixPolicyGrantAdapter(unix_client, GENESIS),
        journal, graph, GENESIS,
    )
    app = FastAPI()
    app.include_router(build_research_idempotent_graph_router(
        entry, verified_basic, UnixPolicyGrantAdapter(unix_client, GENESIS)))
    client = TestClient(app)
    try:
        assert request(client).status_code == 200
        assert request(client).status_code == 409
        assert request(client, value=500).status_code == 409
        assert len(graph.calls) == 1
        op = derive_stable_operation("alice", "stable-request-0001",
                                     "approved_read", {"value": 42})
        pending = list(authority.snapshot().document["pending"].values())
        assert len(pending) == 1
        assert pending[0]["operation"] == op.operation
        assert pending[0]["request_digest"] == op.request_digest
        # Client has only socket path, no policy writer token or KV methods.
        assert not hasattr(unix_client, "txn")
        assert unix_client.__dict__ == {"path": str(tmp_path / "authority.sock")}
    finally:
        stop(server, thread)


def test_http_authenticated_stable_id_remains_pending_after_writer_disappears(tmp_path):
    from test_trace_etcd_policy_writer_research import socket_rig, stop, GENESIS
    from trace_etcd_policy_writer_research import UnixPolicyGrantAdapter

    server, thread, unix_client, authority, witness, operator = socket_rig(tmp_path)
    graph = Graph()
    entry = ProtectedGraphEntry(
        {"approved_read": QueryPlan("RETURN $value AS value", ("value",))},
        UnixPolicyGrantAdapter(unix_client, GENESIS),
        Journal(), graph, GENESIS)
    app = FastAPI()
    app.include_router(build_research_idempotent_graph_router(entry, verified_basic))
    with TestClient(app) as client:
        assert request(client).status_code == 200
        assert len(authority.snapshot().document["pending"]) == 1
        stop(server, thread)
        # No successful response from the now missing writer: HTTP 503
        # must not be converted to an execution retry or freed capacity.
        assert request(client).status_code == 503
        assert len(authority.snapshot().document["pending"]) == 1
        assert len(graph.calls) == 1


def status(client, key="stable-request-0001", identity="alice", password="test-password-only"):
    return client.get(
        f"/_research/graph/requests/{key}", auth=(identity, password),
        headers={"X-Verified-Subject": "alice",
                 "Tailscale-User-Login": "alice"})


def test_read_only_status_is_verified_principal_scoped_and_no_reexecution():
    client, quorum, store, signer, graph, journal = rig()
    assert status(client).json() == {"state": "ABSENT_NOT_AN_EXECUTION_PERMIT"}
    assert request(client).status_code == 200
    assert status(client).json() == {"state": "PENDING_EXECUTION_UNCERTAIN"}
    assert status(client, identity="bob").json() == {
        "state": "ABSENT_NOT_AN_EXECUTION_PERMIT"}
    assert status(client, identity="mallory").status_code == 401
    assert len(graph.calls) == 1
    token = next(iter(quorum.snapshot().document["pending"]))
    quorum.bind(token, "ingress-gateway", 1, "neo4j-disposable",
                "gen-1", "neo4j-transaction-91")
    receipt = witness_receipt(quorum.snapshot(), token)
    quorum.close_with_witness(token, receipt, signer.sign(canon(receipt)))
    assert status(client).json() == {"state": "CLOSED_NO_REEXECUTION"}
    assert len(graph.calls) == 1
    assert "token" not in str(status(client).json())


def test_reconciliation_under_quorum_loss_is_503_not_absent():
    client, quorum, store, signer, graph, journal = rig()
    store.fail = True
    assert status(client).status_code == 503
    assert status(client).json() == {"detail": "reconciliation_unavailable"}


def test_actual_unix_service_returns_scoped_http_status_without_token(tmp_path):
    from test_trace_etcd_policy_writer_research import socket_rig, stop, GENESIS
    from trace_etcd_policy_writer_research import UnixPolicyGrantAdapter

    server, thread, unix_client, authority, witness, operator = socket_rig(tmp_path)
    adapter = UnixPolicyGrantAdapter(unix_client, GENESIS)
    graph = Graph()
    app = FastAPI()
    app.include_router(build_research_idempotent_graph_router(
        ProtectedGraphEntry(
            {"approved_read": QueryPlan("RETURN $value AS value", ("value",))},
            adapter, Journal(), graph, GENESIS),
        verified_basic, adapter))
    client = TestClient(app)
    try:
        assert status(client).json() == {"state": "ABSENT_NOT_AN_EXECUTION_PERMIT"}
        assert request(client).status_code == 200
        assert status(client).json() == {"state": "PENDING_EXECUTION_UNCERTAIN"}
        assert status(client, identity="bob").json() == {
            "state": "ABSENT_NOT_AN_EXECUTION_PERMIT"}
        assert len(graph.calls) == 1
    finally:
        stop(server, thread)
    assert status(client).status_code == 503
