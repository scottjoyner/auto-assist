"""Offline real UNIX-socket policy writer tests (no Docker / etcd / Neo4j).

The fake KV is NOT Raft. These tests prove protocol and local OS credential
containment only; the physical three-host RBAC acceptance is separate.
"""
import os
import multiprocessing as mp
import threading
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from test_trace_etcd_quorum_fence_research import FakeEtcd
from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, FenceRefused, canon, witness_receipt
)
from trace_etcd_policy_writer_research import (
    PolicyEngine, PolicyRefused, UnixPolicyWriterServer,
    UnixPolicyClient, UnixPolicyGrantAdapter, MAX_REQUEST
)
from trace_graph_entry_guarded import ProtectedGraphEntry, QueryPlan
from trace_graph_entry_research import AdmissionDenied

GENESIS = "policy-test-genesis"


def private_engine():
    kv = FakeEtcd()
    operator = Ed25519PrivateKey.generate()
    witness = Ed25519PrivateKey.generate()
    public = lambda signer: signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    authority = EtcdQuorumFence(
        kv, "/assistx/research/fencing/policy-writer-contract")
    authority.bootstrap(
        GENESIS, "gateway-A", public(operator), public(witness), 1)
    return (PolicyEngine(authority, "gateway-A", 1, GENESIS,
                         frozenset({"approved_read"})),
            authority, witness, operator)


def socket_rig(tmp_path, allowed_uid=None):
    os.chmod(tmp_path, 0o700)
    engine, authority, witness, operator = private_engine()
    path = tmp_path / "authority.sock"
    server = UnixPolicyWriterServer(
        path, engine, allowed_uid=(os.getuid() if allowed_uid is None
                                   else allowed_uid))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, UnixPolicyClient(path), authority, witness, operator


def stop(server, thread):
    server.shutdown()
    thread.join(4)
    assert not thread.is_alive()


def test_real_socket_admission_does_not_expose_raw_etcd_token(tmp_path):
    server, thread, client, authority, witness, operator = socket_rig(tmp_path)
    try:
        adapter = UnixPolicyGrantAdapter(client, GENESIS)
        grant = adapter.admit("a" * 32, "approved_read")
        assert len(grant.token) == 32
        assert grant.term == 1 and grant.epoch == GENESIS
        assert grant.token in authority.snapshot().document["pending"]
        assert not hasattr(client, "txn") and not hasattr(client, "range")
        assert not hasattr(adapter, "release")
        assert client.__dict__ == {"path": str(tmp_path / "authority.sock")}
        with pytest.raises(PolicyRefused, match="PHYSICAL_CAPACITY_OCCUPIED"):
            adapter.admit("b" * 32, "approved_read")
    finally:
        stop(server, thread)


@pytest.mark.parametrize("payload", [
    {"kind": "admit", "plan_id": "unregistered", "operation_id": "a" * 32},
    {"kind": "admit", "plan_id": "approved_read",
     "operation_id": "worker-defined-op"},
    {"kind": "admit", "plan_id": "approved_read", "operation_id": "a" * 32,
     "term": 999},
    {"kind": "admit", "plan_id": "approved_read", "operation_id": "a" * 32,
     "cypher": "MATCH (n) DELETE n"},
    {"kind": "raw-txn", "key": "/assistx/research/fencing/policy-writer-contract",
     "value": "TAKEOVER"},
    {"kind": "takeover", "new_owner": "attacker"},
    {"kind": "close", "token": "0" * 32, "signature": "0" * 128,
     "receipt": {}, "force": True},
])
def test_unapproved_actions_and_client_owner_term_injection_denied(tmp_path, payload):
    server, thread, client, authority, witness, operator = socket_rig(tmp_path)
    try:
        with pytest.raises(PolicyRefused):
            client.request(payload)
        assert not authority.snapshot().document["pending"]
        assert authority.snapshot().document["term"] == 1
    finally:
        stop(server, thread)


def test_authenticated_peer_uid_is_required(tmp_path):
    server, thread, client, authority, witness, operator = socket_rig(
        tmp_path, allowed_uid=os.getuid() + 2000)
    try:
        with pytest.raises(PolicyRefused, match="UNAUTHORIZED_UNIX_PEER"):
            client.request({"kind": "status"})
        assert not authority.snapshot().document["pending"]
    finally:
        stop(server, thread)


def test_forged_closure_fails_and_detached_signature_closes_exact(tmp_path):
    server, thread, client, authority, witness, operator = socket_rig(tmp_path)
    try:
        grant = UnixPolicyGrantAdapter(client, GENESIS).admit(
            "1" * 32, "approved_read")
        authority.bind(grant.token, "gateway-A", 1, "server-one", "gen-one",
                       "neo4j-transaction-4")
        receipt = witness_receipt(authority.snapshot(), grant.token)
        bad_signature = Ed25519PrivateKey.generate().sign(canon(receipt)).hex()
        with pytest.raises(PolicyRefused, match="POLICY_QUORUM_UNAVAILABLE_OR_UNCERTAIN"):
            client.request({"kind": "close", "token": grant.token,
                            "receipt": receipt, "signature": bad_signature})
        assert client.request({"kind": "status"})["pending"] == 1
        good = witness.sign(canon(receipt)).hex()
        assert client.request({
            "kind": "close", "token": grant.token,
            "receipt": receipt, "signature": good
        }) == {"closed": True}
        assert client.request({"kind": "status"})["pending"] == 0
        with pytest.raises(PolicyRefused):
            client.request({"kind": "close", "token": grant.token,
                            "receipt": receipt, "signature": good})
    finally:
        stop(server, thread)


def test_gateway_enters_reviewed_graph_plan_through_socket_only(tmp_path):
    server, thread, client, authority, witness, operator = socket_rig(tmp_path)
    calls = []
    class Journal:
        def append(self, kind, attempt, plan_id):
            calls.append(("event", kind))
    class Graph:
        def execute(self, attempt, cypher, params):
            calls.append(("graph", cypher))
            return {"answer": params["value"]}
    try:
        gateway = ProtectedGraphEntry(
            {"approved_read": QueryPlan("RETURN $value AS value", ("value",))},
            UnixPolicyGrantAdapter(client, GENESIS),
            Journal(), Graph(), GENESIS)
        assert gateway.execute("approved_read", {"value": 42}) == {"answer": 42}
        assert [k for k, _ in calls] == ["event", "event", "graph", "event"]
        assert client.request({"kind": "status"})["pending"] == 1
        with pytest.raises(AdmissionDenied, match="UNREGISTERED_QUERY_PLAN"):
            gateway.execute("MATCH (n) DELETE n", {})
        assert len([x for x in calls if x[0] == "graph"]) == 1
    finally:
        stop(server, thread)


def test_large_request_and_non_private_socket_dir_denied(tmp_path):
    non_private = tmp_path / "public"
    non_private.mkdir(mode=0o755)
    os.chmod(non_private, 0o755)
    engine, authority, witness, operator = private_engine()
    with pytest.raises(ValueError, match="REQUIRE_PRIVATE"):
        UnixPolicyWriterServer(non_private / "policy.sock", engine, os.getuid())
    os.chmod(tmp_path, 0o700)
    client = UnixPolicyClient(tmp_path / "missing.sock")
    with pytest.raises(PolicyRefused, match="REQUEST_EXCEEDS_IPC_BOUND"):
        client.request({"kind": "admit", "data": "x" * (MAX_REQUEST + 1)})
    with pytest.raises(PolicyRefused, match="WRITER_UNAVAILABLE"):
        client.request({"kind": "status"})


def _policy_process(socket_path, signal):
    engine, authority, witness, operator = private_engine()
    server = UnixPolicyWriterServer(socket_path, engine, os.getuid())
    signal.send("ready")
    server.serve_forever()


def test_policy_writer_can_run_as_separate_os_process(tmp_path):
    os.chmod(tmp_path, 0o700)
    ctx = mp.get_context("fork")
    parent, child = ctx.Pipe()
    path = tmp_path / "child-policy.sock"
    proc = ctx.Process(target=_policy_process, args=(path, child))
    proc.start()
    try:
        assert parent.poll(8) and parent.recv() == "ready"
        client = UnixPolicyClient(path)
        grant = UnixPolicyGrantAdapter(client, GENESIS).admit(
            "e" * 32, "approved_read")
        assert grant.reservation_id
        assert client.request({"kind": "status"})["pending"] == 1
    finally:
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(2)
        assert not proc.is_alive()
        path.unlink(missing_ok=True)
