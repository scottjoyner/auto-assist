"""Offline etcd Raft CAS contract; fakes cannot prove physical quorum.

The physical three-host experiment is separate and explicitly opted-in.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from threading import Lock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, EtcdTLS, FenceRefused, b64, canon,
    takeover_request, witness_receipt
)


class FakeEtcd:
    def __init__(self, cluster="12345"):
        self.lock = Lock()
        self.cluster = cluster
        self.revision = 1
        self.values = {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise FenceRefused("QUORUM_UNAVAILABLE_OR_OUTCOME_UNCERTAIN")

    def range(self, key):
        with self.lock:
            self._check()
            stored = self.values.get(key)
            result = {"header": {"cluster_id": self.cluster}}
            if stored:
                value, revision = stored
                result["kvs"] = [{
                    "value": b64(value),
                    "mod_revision": str(revision)
                }]
            return result

    def txn(self, req):
        with self.lock:
            self._check()
            compare = req["compare"][0]
            key = base64.b64decode(compare["key"]).decode()
            record = self.values.get(key)
            if compare["target"] == "VERSION":
                succeeded = record is None and int(compare["version"]) == 0
            else:
                succeeded = (
                    record is not None and
                    record[1] == int(compare["mod_revision"]))
            if succeeded and req["success"]:
                put = req["success"][0]["request_put"]
                new_value = base64.b64decode(put["value"])
                self.revision += 1
                self.values[key] = (new_value, self.revision)
            return {
                "succeeded": succeeded,
                "header": {"cluster_id": self.cluster,
                           "revision": str(self.revision)}
            }


def rig(capacity=1):
    store = FakeEtcd()
    signer = Ed25519PrivateKey.generate()
    operator = Ed25519PrivateKey.generate()
    def public(k):
        return k.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    auth = EtcdQuorumFence(store, "/assistx/research/fencing/offline")
    cluster = auth.bootstrap("genesis-unit", "gateway-a", public(operator),
                             public(signer), capacity)
    return auth, store, signer, operator, cluster


def test_create_only_genesis_and_two_endpoints_share_cap():
    auth, store, signer, operator, cluster = rig()
    assert cluster == "12345"
    with pytest.raises(FenceRefused, match="AUTHORITY_ALREADY_EXISTS"):
        auth.bootstrap("new", "intruder", b"x" * 32, b"y" * 32)
    a = EtcdQuorumFence(store, auth.key, cluster)
    assert a.admit("gateway-a", 1, "operation-a")
    with pytest.raises(FenceRefused, match="PHYSICAL_CAPACITY_OCCUPIED"):
        auth.admit("gateway-a", 1, "operation-b")


def test_no_takeover_before_verified_physical_closure():
    auth, store, signer, operator, cluster = rig()
    token = auth.admit("gateway-a", 1, "long-lived")
    req = takeover_request(auth.snapshot(), "gateway-b")
    with pytest.raises(FenceRefused, match="UNCERTAIN_PHYSICAL_WORK"):
        auth.takeover("gateway-b", req, operator.sign(canon(req)))
    with pytest.raises(FenceRefused, match="NO_VERIFIED_PHYSICAL_BINDING"):
        auth.close_with_witness(token, {}, b"")
    assert token in auth.snapshot().document["pending"]
    auth.bind(token, "gateway-a", 1, "disposable-neo4j", "gen-1",
              "neo4j-transaction-42")
    receipt = witness_receipt(auth.snapshot(), token)
    signature = signer.sign(canon(receipt))
    with pytest.raises(FenceRefused, match="MISMATCHED_WITNESS_RECEIPT"):
        auth.close_with_witness(token, dict(receipt, term=2), signature)
    assert token in auth.snapshot().document["pending"]
    auth.close_with_witness(token, receipt, signature)
    with pytest.raises(FenceRefused):
        auth.close_with_witness(token, receipt, signature)
    assert auth.snapshot().document["pending"] == {}


def test_explicit_signed_owner_term_transition_and_stale_replay():
    auth, store, signer, operator, cluster = rig()
    req = takeover_request(auth.snapshot(), "gateway-b")
    with pytest.raises(FenceRefused, match="INVALID_OPERATOR_SIGNATURE"):
        auth.takeover("gateway-b", req, signer.sign(canon(req)))
    assert auth.takeover("gateway-b", req, operator.sign(canon(req))) == 2
    with pytest.raises(FenceRefused, match="INVALID_OPERATOR_APPROVAL"):
        auth.takeover("gateway-c", req, operator.sign(canon(req)))
    with pytest.raises(FenceRefused, match="STALE_TERM_OR_OWNER"):
        auth.admit("gateway-a", 1, "stale")
    assert auth.admit("gateway-b", 2, "new-work")


def test_linearizable_read_and_commit_fail_closed_on_quorum_loss():
    auth, store, signer, operator, cluster = rig()
    store.fail = True
    with pytest.raises(FenceRefused, match="QUORUM_UNAVAILABLE"):
        auth.snapshot()
    with pytest.raises(FenceRefused, match="QUORUM_UNAVAILABLE"):
        auth.admit("gateway-a", 1, "blocked")
    assert not any(b"blocked" in raw[0] for raw in store.values.values())


def test_wrong_cluster_endpoint_refuses_application_grant():
    auth, store, signer, operator, cluster = rig()
    wrong = EtcdQuorumFence(store, auth.key, "CLONED-CLUSTER")
    with pytest.raises(FenceRefused, match="WRONG_RAFT_CLUSTER"):
        wrong.snapshot()


def test_real_atomic_mod_revision_rejects_competing_writes():
    auth, store, signer, operator, cluster = rig(capacity=2)
    def do(i):
        try:
            return auth.admit("gateway-a", 1, f"op-{i}")
        except FenceRefused:
            return None
    with ThreadPoolExecutor(max_workers=12) as workers:
        results = list(workers.map(do, range(12)))
    assert 0 < len([x for x in results if x]) <= 2
    assert len(auth.snapshot().document["pending"]) <= 2


def test_copy_both_fake_raft_stores_is_counterexample_not_consensus():
    auth, store, signer, operator, cluster = rig()
    other = FakeEtcd("COPIED-CLUSTER")
    other.values = copy.deepcopy(store.values)
    a = auth.admit("gateway-a", 1, "same-job")
    b = EtcdQuorumFence(other, auth.key).admit("gateway-a", 1, "same-job")
    assert a != b
    # Explicitly proves that copying an already bootstrapped keyspace and
    # independently accepting requests is NOT one Raft consensus group.


def test_https_only_and_bad_namespace_rejected():
    with pytest.raises(ValueError, match="REQUIRE_ETCD_TLS"):
        EtcdTLS("http://127.0.0.1:2379", "ca", "cert", "key")
    with pytest.raises(ValueError, match="NOT_ISOLATED"):
        EtcdQuorumFence(FakeEtcd(), "/prod/authority")
