"""Opt-in live three-voter Raft CAS acknowledgement-loss negative.

This simulates HTTP reply loss *after* a real etcd /v3/kv/txn commit:
we forward the write to etcd, then discard the success response. It is not
a real transport partition, and does NOT simulate actual Bolt execution.
The reservation remains pending and is never auto-released.
"""
from __future__ import annotations
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, FenceRefused
)


class DropSuccessfulAck:
    def __init__(self, actual):
        self.actual = actual
        self.drop_next = True

    def range(self, key):
        return self.actual.range(key)

    def txn(self, request):
        reply = self.actual.txn(request)
        if self.drop_next and reply.get("succeeded") is True:
            self.drop_next = False
            raise FenceRefused("QUORUM_UNAVAILABLE_OR_OUTCOME_UNCERTAIN")
        return reply


def run_ack_loss(clients, cluster_id, key):
    witness = Ed25519PrivateKey.generate()
    operator = Ed25519PrivateKey.generate()
    public = lambda signer: signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    owner = "gateway-ack-research"
    EtcdQuorumFence(
        clients["r1"], key, cluster_id
    ).bootstrap("real-quorum-ack-fixture", owner, public(operator),
                public(witness), capacity=1)
    abandoned_ack = EtcdQuorumFence(
        DropSuccessfulAck(clients["r1"]), key, cluster_id)
    stable = "reviewed_plan:authenticated-stable-request-0001"
    try:
        abandoned_ack.admit(owner, 1, stable)
    except FenceRefused as exc:
        if str(exc) != "QUORUM_UNAVAILABLE_OR_OUTCOME_UNCERTAIN":
            raise
    else:
        raise AssertionError("ACK_LOSS_INJECTION_NOT_FIRED")

    independent = EtcdQuorumFence(clients["r2"], key, cluster_id)
    state = independent.reconcile_operation(stable)
    assert state["state"] == "PENDING_EXECUTION_UNCERTAIN", state
    assert state["attempt_state"] == "RESERVED"
    assert len(independent.snapshot().document["pending"]) == 1
    try:
        independent.admit(owner, 1, stable)
    except FenceRefused as exc:
        assert str(exc) == "EXISTING_OPERATION_REQUIRES_RECONCILIATION"
    else:
        raise AssertionError("DUPLICATE_INFLIGHT_OPERATION_ADMITTED")
    try:
        independent.admit(owner, 1, "reviewed_plan:other-operation")
    except FenceRefused as exc:
        assert str(exc) == "PHYSICAL_CAPACITY_OCCUPIED"
    else:
        raise AssertionError("AMBIGUOUS_CAPACITY_WAS_RELEASED")
    return {
        "real_quorum_cas_success_response_lost": True,
        "independent_voter_reconciled_exact_request": True,
        "pending_reservation_held_after_uncertain_ack": True,
        "same_operation_replay_refused": True,
        "different_operation_blocked_at_capacity": True,
        "client_request_identity_cross_retry_wired": False,
        "real_http_network_partition_injected": False,
        "neo4j_transaction_tested_in_this_probe": False,
        "production_authority": False,
    }
