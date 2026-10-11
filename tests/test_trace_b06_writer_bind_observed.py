"""B06 original-transaction binding negatives, offline-only, zero Docker."""
from copy import deepcopy
import pytest

from test_trace_disposable_separate_policy_writer import writer_policy
from trace_b06_writer_bind_observed import (
    BindingRefused, checked_observation, run,
)
from trace_etcd_quorum_fence_research import FenceRefused


def ready():
    writer, q = writer_policy()
    op = "auth:" + "a"*64
    token = writer.admit({
        "plan_id": "get_task_read",
        "operation_id": op,
        "request_digest": "a"*64,
    })
    meta = {
        "assistx_schema": "research-gateway-entry-v1",
        "assistx_operation_id": op,
        "assistx_attempt_id": "b06-" + token,
        "assistx_reservation_id": token,
        "assistx_epoch": "genesis-ack",
        "assistx_term": 1,
        "assistx_plan_id": "get_task_read",
    }
    target = {
        "metadata": meta, "graph_member": "disposable-neo4j",
        "graph_boot": "unattested-fixture-boot",
    }
    evidence = {
        "txid": "neo4j-transaction-17",
        "observed_sequence": 1,
        "observed_row": {
            "transactionId": "neo4j-transaction-17",
            "database": "neo4j", "metaData": deepcopy(meta),
        },
    }
    return q, token, target, evidence


def test_writer_binds_one_exact_original_active_observation_and_keeps_capacity():
    q, token, target, evidence = ready()
    assert run("bad-txid", target, evidence, q)[
        "forged_observation_denied"]
    assert q.snapshot().document["pending"][token]["state"] == "RESERVED"
    result = run("bind", target, evidence, q)
    assert result == {
        "exact_original_transaction_raft_bound": True, "state": "STARTED",
        "generation_attested": False,
        "signer_authorized": False, "release_authorized": False}
    view = q.snapshot().document
    assert view["pending"][token]["txid"] == "neo4j-transaction-17"
    assert view["pending"][token]["state"] == "STARTED"
    assert view["completed"] == {}
    assert run("replay", target, evidence, q)["duplicate_binding_denied"]
    assert len(q.snapshot().document["pending"]) == 1


@pytest.mark.parametrize("mutator", [
    lambda t,e: e.update(txid="neo4j-transaction-777"),
    lambda t,e: e["observed_row"].update(transactionId="neo4j-transaction-7"),
    lambda t,e: e["observed_row"].update(database="system"),
    lambda t,e: e["observed_row"]["metaData"].update(assistx_term=2),
    lambda t,e: e["observed_row"]["metaData"].update(assistx_operation_id="auth:"+"b"*64),
    lambda t,e: e.update(observed_sequence=0),
    lambda t,e: t["metadata"].update(assistx_term=2),
    lambda t,e: t["metadata"].update(assistx_epoch="stale-genesis"),
    lambda t,e: t["metadata"].update(assistx_attempt_id="forged"),
    lambda t,e: t.update(graph_boot="changed-boot"),
    lambda t,e: t.update(graph_member="other-neo4j"),
    lambda t,e: t["metadata"].update(assistx_plan_id="DELETE"),
    lambda t,e: e.update(extra_undeclared_receipt=True),
])
def test_forged_or_stale_observations_refused_without_raft_mutation(mutator):
    q, token, target, evidence = ready()
    before = q.snapshot().document
    mutator(target, evidence)
    with pytest.raises(BindingRefused):
        checked_observation(target, evidence, q)
    assert q.snapshot().document == before
    assert q.snapshot().document["pending"][token]["state"] == "RESERVED"


def test_different_actual_raft_owner_refused():
    q, token, target, evidence = ready()
    before = q.snapshot()
    changed = deepcopy(before.document)
    changed["owner"] = "attacker"
    q._write(before, changed)
    with pytest.raises(BindingRefused):
        checked_observation(target, evidence, q)


def test_missing_original_raft_reservation_refused():
    q, token, target, evidence = ready()
    before = q.snapshot()
    changed = deepcopy(before.document)
    del changed["pending"][token]
    q._write(before, changed)
    with pytest.raises(BindingRefused):
        checked_observation(target, evidence, q)


def test_even_after_binding_unsiged_closure_still_denied():
    q, token, target, evidence = ready()
    run("bind", target, evidence, q)
    with pytest.raises(FenceRefused, match="MISMATCHED_WITNESS_RECEIPT"):
        q.close_with_witness(token, {"schema": "forged"}, b"fake")
    assert token in q.snapshot().document["pending"]
    assert not q.snapshot().document["completed"]


def test_only_reviewed_modes_allowed_and_no_close_api_in_script():
    q, token, target, evidence = ready()
    with pytest.raises(BindingRefused, match="UNREVIEWED_BIND_MODE"):
        run("force-release", target, evidence, q)
    from pathlib import Path
    src = Path(__file__).with_name(
        "trace_b06_writer_bind_observed.py").read_text()
    assert "authority.bind(" in src
    assert "close_with_witness(" not in src
    assert "witness_receipt(" not in src
    assert "Ed25519PrivateKey" not in src
    assert '"release_authorized": False' in src
