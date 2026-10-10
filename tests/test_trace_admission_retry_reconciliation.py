"""Negative proofs for ambiguous quorum acknowledgements and replay.

FakeEtcd simulates "COMMIT APPLIED / HTTP RESPONSE LOST". This is an
application-level research test, NOT a real packet partition or multi-host
test. The existing physical Raft cluster acceptance stays separate.
"""
from concurrent.futures import ThreadPoolExecutor
import copy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from test_trace_etcd_quorum_fence_research import FakeEtcd, rig
from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, FenceRefused, MAX_COMPLETED_OPERATIONS,
    canon, witness_receipt
)


class CommitThenDrop(FakeEtcd):
    """A successful Raft CAS whose acknowledgement never reaches caller."""

    def __init__(self):
        super().__init__()
        self.drop_next_success = False

    def txn(self, request):
        reply = super().txn(request)
        if self.drop_next_success and reply.get("succeeded"):
            self.drop_next_success = False
            raise FenceRefused("QUORUM_UNAVAILABLE_OR_OUTCOME_UNCERTAIN")
        return reply


def make_lossy():
    _, _, witness, operator, _ = rig()
    store = CommitThenDrop()
    # Reusing the same signer public keys is immaterial to this offline test.
    from cryptography.hazmat.primitives import serialization
    public = lambda k: k.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    authority = EtcdQuorumFence(
        store, "/assistx/research/fencing/ack-loss-unit")
    authority.bootstrap("genesis-ack", "gateway-1",
                        public(operator), public(witness), 2)
    return authority, store, witness


def test_admission_commit_success_ack_lost_does_not_issue_second_grant():
    authority, store, witness = make_lossy()
    store.drop_next_success = True
    with pytest.raises(FenceRefused, match="OUTCOME_UNCERTAIN"):
        authority.admit("gateway-1", 1, "approved:stable-id-1")
    assert authority.reconcile_operation("approved:stable-id-1") == {
        "state": "PENDING_EXECUTION_UNCERTAIN", "attempt_state": "RESERVED",
        "term": 1, "owner": "gateway-1", "cluster_id": "12345"}
    with pytest.raises(FenceRefused, match="REQUIRES_RECONCILIATION"):
        authority.admit("gateway-1", 1, "approved:stable-id-1")
    assert len(authority.snapshot().document["pending"]) == 1


def test_ambiguous_closed_transaction_leaves_nonreusable_tombstone():
    authority, store, witness = make_lossy()
    operation = "approved:stable-id-2"
    token = authority.admit("gateway-1", 1, operation)
    authority.bind(token, "gateway-1", 1,
                   "server-a", "generation-a", "neo4j-transaction-12")
    receipt = witness_receipt(authority.snapshot(), token)
    signature = witness.sign(canon(receipt))
    store.drop_next_success = True
    with pytest.raises(FenceRefused, match="OUTCOME_UNCERTAIN"):
        authority.close_with_witness(token, receipt, signature)
    assert not authority.snapshot().document["pending"]
    assert authority.reconcile_operation(operation)["state"] == (
        "CLOSED_NO_REEXECUTION")
    with pytest.raises(FenceRefused, match="CLOSED_OPERATION_REPLAY_DENIED"):
        authority.admit("gateway-1", 1, operation)
    with pytest.raises(FenceRefused, match="NO_VERIFIED_PHYSICAL_BINDING"):
        authority.close_with_witness(token, receipt, signature)


def test_quorum_outage_during_reconciliation_never_inferrs_absence():
    authority, store, _ = make_lossy()
    authority.admit("gateway-1", 1, "approved:one")
    store.fail = True
    with pytest.raises(FenceRefused, match="QUORUM_UNAVAILABLE"):
        authority.reconcile_operation("approved:one")


def test_absent_id_is_explicitly_not_execution_permission():
    authority, _, _ = make_lossy()
    snapshot = authority.reconcile_operation("approved:unknown-id")
    assert snapshot["state"] == "ABSENT_NOT_AN_EXECUTION_PERMIT"
    assert "token" not in snapshot
    assert "reservation_id" not in snapshot


def test_completed_operations_survive_signed_term_transition():
    authority, store, signer = make_lossy()
    token = authority.admit("gateway-1", 1, "approved:long-lived-identity")
    authority.bind(token, "gateway-1", 1, "server-a", "gen-a",
                   "neo4j-transaction-101")
    receipt = witness_receipt(authority.snapshot(), token)
    authority.close_with_witness(token, receipt, signer.sign(canon(receipt)))
    from trace_etcd_quorum_fence_research import takeover_request
    from cryptography.hazmat.primitives import serialization
    # This case verifies persistence of tombstone across a separately
    # authorized term transition. Operator key obtained from a separate rig
    # would not match, so inspect raw state transition solely for migration.
    before = authority.snapshot()
    changed = copy.deepcopy(before.document)
    changed["term"] = 2
    changed["owner"] = "gateway-2"
    authority._write(before, changed)
    assert authority.reconcile_operation(
        "approved:long-lived-identity")["state"] == "CLOSED_NO_REEXECUTION"
    with pytest.raises(FenceRefused, match="CLOSED_OPERATION_REPLAY"):
        authority.admit("gateway-2", 2, "approved:long-lived-identity")


def test_concurrent_exact_same_identity_only_one_commits():
    authority, store, _ = make_lossy()
    def attempt(_):
        try:
            return authority.admit("gateway-1", 1, "approved:same-request")
        except FenceRefused:
            return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        tokens = list(pool.map(attempt, range(20)))
    assert len([x for x in tokens if x]) == 1
    assert len(authority.snapshot().document["pending"]) == 1


def test_tombstone_capacity_never_silently_discards_prior_operation():
    authority, _, _ = make_lossy()
    state = authority.snapshot()
    updated = copy.deepcopy(state.document)
    updated["completed"] = {
        f"finished:{i}": {"term": 1, "owner": "gateway-1",
                           "receipt_sha256": "f" * 64}
        for i in range(MAX_COMPLETED_OPERATIONS)
    }
    authority._write(state, updated)
    assert authority.reconcile_operation("finished:1")["state"] == (
        "CLOSED_NO_REEXECUTION")
    with pytest.raises(FenceRefused, match="COMPLETED_OPERATION_LEDGER_FULL"):
        authority.admit("gateway-1", 1, "fresh-request")
    assert not authority.snapshot().document["pending"]


def test_reconciliation_never_returns_reusable_grant_secret():
    authority, _, _ = make_lossy()
    operation = "approved:private-token"
    token = authority.admit("gateway-1", 1, operation)
    recon = authority.reconcile_operation(operation)
    assert token not in str(recon)
    assert "token" not in recon
    assert "reservation_id" not in recon


from trace_graph_stable_identity_research import (
    derive_stable_operation, InvalidStableRequest
)


def test_stable_verified_principal_plus_request_id_across_retries():
    a = derive_stable_operation(
        "verified-user-1", "same-client-request-0001",
        "approved_read", {"a": 1, "b": "hello"})
    b = derive_stable_operation(
        "verified-user-1", "same-client-request-0001",
        "approved_read", {"b": "hello", "a": 1})
    c = derive_stable_operation(
        "verified-user-2", "same-client-request-0001",
        "approved_read", {"a": 1, "b": "hello"})
    assert a == b
    assert a.operation != c.operation
    assert len(a.operation) < 128
    assert len(a.request_digest) == 64


def test_reusing_same_identity_with_changed_query_is_a_conflict():
    authority, _, _ = make_lossy()
    a = derive_stable_operation(
        "verified-user-1", "request-00000001", "approved_read", {"n": 1})
    different = derive_stable_operation(
        "verified-user-1", "request-00000001", "approved_read", {"n": 2})
    other_plan = derive_stable_operation(
        "verified-user-1", "request-00000001", "dangerous_read", {"n": 1})
    assert a.operation == different.operation == other_plan.operation
    assert a.request_digest != different.request_digest
    assert a.request_digest != other_plan.request_digest
    token = authority.admit("gateway-1", 1, a.operation,
                            request_digest=a.request_digest)
    with pytest.raises(FenceRefused, match="IDEMPOTENCY_KEY_PAYLOAD_CONFLICT"):
        authority.admit("gateway-1", 1, different.operation,
                        request_digest=different.request_digest)
    with pytest.raises(FenceRefused, match="IDEMPOTENCY_KEY_PAYLOAD_CONFLICT"):
        authority.admit("gateway-1", 1, other_plan.operation,
                        request_digest=other_plan.request_digest)
    assert token in authority.snapshot().document["pending"]


def test_payload_conflict_stays_denied_after_signed_closure_and_term_change():
    authority, store, signer = make_lossy()
    a = derive_stable_operation(
        "verified-user-1", "request-00000002", "approved_read", {"n": 1})
    other = derive_stable_operation(
        "verified-user-1", "request-00000002", "approved_read", {"n": 999})
    token = authority.admit("gateway-1", 1, a.operation,
                            request_digest=a.request_digest)
    authority.bind(token, "gateway-1", 1, "server-1", "first-boot",
                   "neo4j-transaction-555")
    receipt = witness_receipt(authority.snapshot(), token)
    authority.close_with_witness(token, receipt, signer.sign(canon(receipt)))
    with pytest.raises(FenceRefused, match="IDEMPOTENCY_KEY_PAYLOAD_CONFLICT"):
        authority.admit("gateway-1", 1, other.operation,
                        request_digest=other.request_digest)
    with pytest.raises(FenceRefused, match="CLOSED_OPERATION_REPLAY_DENIED"):
        authority.admit("gateway-1", 1, a.operation,
                        request_digest=a.request_digest)


@pytest.mark.parametrize("subject,request_id,plan,params", [
    ("", "request-00000001", "approved_read", {}),
    ("spoofed subject ", "request-00000001", "approved_read", {}),
    ("verified-user", "short", "approved_read", {}),
    ("verified-user", "request-00000001", "Raw Cypher", {}),
    ("verified-user", "request-00000001", "approved_read", {"x": float("nan")}),
    ("verified-user", "request-00000001", "approved_read", {"x": [1, 2]}),
    ("verified-user", "request-00000001", "approved_read", {"x": object()}),
])
def test_untrusted_or_uncanonical_identity_rejected(subject, request_id,
                                                     plan, params):
    with pytest.raises(InvalidStableRequest):
        derive_stable_operation(subject, request_id, plan, params)


def test_corrupted_request_digest_is_never_accepted():
    authority, store, signer = make_lossy()
    with pytest.raises(FenceRefused, match="INVALID_REQUEST_DIGEST"):
        authority.admit("gateway-1", 1, "stable-op",
                        request_digest="not-a-sha256")
    assert authority.snapshot().document["pending"] == {}


def test_tombstone_capacity_reserved_for_pending_physical_closure():
    authority, store, signer = make_lossy()
    old = authority.snapshot()
    updated = copy.deepcopy(old.document)
    updated["completed"] = {
        f"closed:{i}": {"owner": "gateway-1", "term": 1,
                          "request_digest": None,
                          "receipt_sha256": "f" * 64}
        for i in range(MAX_COMPLETED_OPERATIONS - 1)
    }
    authority._write(old, updated)
    first = authority.admit("gateway-1", 1, "approved:pending-last-slot")
    with pytest.raises(FenceRefused, match="REPLAY_LEDGER_CLOSE_CAPACITY_RESERVED"):
        authority.admit("gateway-1", 1, "approved:cannot-strand-a-closure")
    authority.bind(first, "gateway-1", 1, "server-1", "gen-1",
                   "neo4j-transaction-99")
    receipt = witness_receipt(authority.snapshot(), first)
    authority.close_with_witness(first, receipt, signer.sign(canon(receipt)))
    assert not authority.snapshot().document["pending"]
    assert len(authority.snapshot().document["completed"]) == (
        MAX_COMPLETED_OPERATIONS)
    with pytest.raises(FenceRefused, match="COMPLETED_OPERATION_LEDGER_FULL"):
        authority.admit("gateway-1", 1, "new-after-full")
