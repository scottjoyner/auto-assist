"""B06: offline negatives for transaction binding, graph restart and forged closure.

A finished HTTP task lookup is NOT a transaction witness or release permit.
The physically joined three-host broker in this slice leaves capacity held.
"""
from pathlib import Path

import pytest

from trace_etcd_quorum_fence_research import FenceRefused
from test_trace_disposable_separate_policy_writer import writer_policy


def test_unbound_reservation_rejects_forged_closure_and_keeps_capacity():
    policy, q = writer_policy()
    token = policy.admit({
        "plan_id": "get_task_read",
        "operation_id": "auth:" + "d" * 64,
        "request_digest": "a" * 64,
    })
    view = q.snapshot()
    assert view.document["pending"][token]["state"] == "RESERVED"
    assert view.document["pending"][token]["txid"] is None
    with pytest.raises(FenceRefused, match="NO_VERIFIED_PHYSICAL_BINDING"):
        q.close_with_witness(token, {
            "schema": "assistx-raft-closure-research-v1",
            "token": token,
            "observation": "external-verified-closure",
        }, b"not-an-independent-signature")
    after = q.snapshot().document
    assert token in after["pending"]
    assert not after["completed"]


def test_graph_http_success_does_not_create_physical_binding():
    source = Path(__file__).with_name(
        "trace_disposable_task_broker.py").read_text()
    assert 'GRAPH_ENDPOINT = "http://neo4j:7474/db/neo4j/tx/commit"' in source
    assert "self.server.raft_admission.admit(" in source
    assert "task = fetch_task(" in source
    assert "self.reply(200, {\"task\": task})" in source
    assert "close_with_witness(" not in source
    assert "quorum.bind(" not in source


def test_physical_negative_targets_only_disposable_graph_and_stays_fail_closed():
    source = Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    assert 'ASSISTX_B06_GRAPH_RESTART_RESEARCH' in source
    assert 'docker("stop", "-t", "2", graph' in source
    assert 'docker("start", graph' in source
    assert 'GRAPH_OUTAGE_FREED_RAFT_CAPACITY' in source
    assert 'GRAPH_RESTART_PREMATURE_CLOSURE' in source
    assert '"b06_independent_witness_integrated": False' in source
    assert 'if (remote and os.getenv("ASSISTX_B06_GRAPH_RESTART_RESEARCH")' in source


def test_no_physical_witness_without_original_server_generation_and_txid():
    physical = Path(__file__).with_name(
        "probe_trace_etcd_rbac_physical.py").read_text()
    assert "NO_VERIFIED_PHYSICAL_BINDING" in physical
    assert "UNOBSERVED_CLOSURE_ALLOWED" in physical
    assert "FORGED_CLOSURE_CHANGED_RESERVATIONS" in physical
    assert 'final_view.document["pending"]' in physical
    assert "joined_key" in physical
