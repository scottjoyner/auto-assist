"""Fail-closed source and fake-KV tests for same-host joined research canary.

Hosted CI never creates containers or changes production graph/network state.
"""
from pathlib import Path
import pytest

from test_trace_admission_retry_reconciliation import make_lossy
from trace_disposable_task_broker import JoinedRaftAdmission, RequestDenied
from trace_etcd_quorum_fence_research import FenceRefused


def controller():
    authority, store, signer = make_lossy()
    ctrl = object.__new__(JoinedRaftAdmission)
    ctrl.quorum = authority
    ctrl.genesis = "genesis-ack"
    ctrl.owner = "gateway-1"
    ctrl.term = 1
    return ctrl, authority, store


def test_actual_cas_replayed_stable_id_is_not_accepted_twice():
    ctrl, authority, store = controller()
    key = "research-joined-operation-0001"
    token = ctrl.admit(key, "get_task_read", "fixture-task-abcd")
    assert token in authority.snapshot().document["pending"]
    with pytest.raises(FenceRefused, match="EXISTING_OPERATION_REQUIRES"):
        ctrl.admit(key, "get_task_read", "fixture-task-abcd")
    with pytest.raises(FenceRefused, match="IDEMPOTENCY_KEY_PAYLOAD_CONFLICT"):
        ctrl.admit(key, "get_task_claim_guard", "fixture-task-abcd")
    assert len(authority.snapshot().document["pending"]) == 1


def test_quorum_read_failure_and_bad_key_cannot_issue_grant():
    ctrl, authority, store = controller()
    with pytest.raises(RequestDenied, match="INVALID_IDEMPOTENCY_KEY"):
        ctrl.admit(None, "get_task_read", "fixture-task-abcd")
    store.fail = True
    with pytest.raises(FenceRefused, match="QUORUM_UNAVAILABLE"):
        ctrl.admit("research-joined-operation-0001",
                   "get_task_read", "fixture-task-abcd")


def test_changed_pinned_owner_is_fail_closed():
    ctrl, authority, store = controller()
    ctrl.owner = "unexpected-gateway"
    with pytest.raises(FenceRefused, match="STALE_RAFT_EPOCH_OR_OWNER"):
        ctrl.admit("research-joined-operation-0001",
                   "get_task_read", "fixture-task-abcd")


def test_broker_does_not_run_physical_graph_before_committed_admission():
    body = Path(__file__).with_name("trace_disposable_task_broker.py").read_text()
    assert body.index("self.server.raft_admission.admit(") < body.index(
        "task = fetch_task(")
    assert "ALREADY_SUBMITTED_OR_CONFLICT" in body
    assert "ADMISSION_UNCERTAIN" in body
    assert "server.raft_admission = (" in body
    assert "self.server.raft_admission" in body
    assert "self.server.reader_password" in body
    # No release based on return status or worker response success:
    assert "close_with_witness(" not in body
    assert "release_reservation(" not in body


def test_disposable_cluster_only_one_host_and_explicit_opt_in():
    physical = Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    assert 'ASSISTX_DISPOSABLE_RAFT_IN_BROKER' in physical
    assert 'ASSISTX_DISPOSABLE_GRAPH_BROKER' in physical
    assert '("--network", "connect", raft_net, broker)' not in physical
    assert 'docker("network", "connect", raft_net, broker)' in physical
    assert "if joined else (graph_net, worker_net)" in physical
    assert "DISPOSABLE_RAFT_CLUSTER_ID_MISSING" in physical
    assert "INVALID_JOINED_RAFT_PENDING_COUNTS" in physical
    assert '"disposable_real_raft_voters_same_host": joined' in physical
    assert '"independent_physical_witness": False' in physical


def test_no_production_identity_or_standalone_reader_permission_claim():
    body = Path(__file__).with_name(
        "trace_disposable_joined_raft_worker.py").read_text()
    assert '"single_host_Raft_not_independent_failure_domains":True' in body
    assert 'assert not connect(CONFIG["neo4j_ip"],7687)' in body
    assert 'assert not connect(CONFIG["quorum_ip"],2379)' in body
    assert "no_witness_or_automatic_capacity_release" in body
    assert "production_deployed" in body


def test_joined_majority_loss_sends_no_graph_request():
    physical = Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    worker = Path(__file__).with_name(
        "trace_disposable_joined_quorum_loss.py").read_text()
    assert '"assistx-broker-quorum-r" + str(voter_i) + "-" + tag' in physical
    assert "for voter_i in (2, 3):" in physical
    assert "MINORITY_BROKER_DID_NOT_FAIL_CLOSED" in physical
    assert 'status==503' in worker
    assert '"error":"ADMISSION_UNCERTAIN"' in worker
    assert '"production_authority":False' in worker
