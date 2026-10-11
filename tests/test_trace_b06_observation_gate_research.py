"""Offline negatives for research-only B06 transaction identity observer."""
from dataclasses import replace
import pytest

from trace_b06_observation_gate_research import (
    META_KEYS, PinnedGraphTarget, GraphObservation, ObservationRejected,
    capture_active, observe_absence_only,
)


def target():
    meta = {
        "assistx_schema": "research-gateway-entry-v1",
        "assistx_operation_id": "auth:" + "a"*64,
        "assistx_attempt_id": "attempt-a",
        "assistx_reservation_id": "b"*32,
        "assistx_epoch": "fresh-three-host-genesis",
        "assistx_term": 1,
        "assistx_plan_id": "get_task_read",
    }
    return PinnedGraphTarget(
        "disposable-member-0", "disposable-boot-0",
        "neo4j", "b"*32, meta, "fixture-broker")


def observation(seq, rows=(), **kwargs):
    return GraphObservation("independent-observer", "disposable-member-0",
                            "disposable-boot-0", seq, tuple(rows), **kwargs)


def row(meta=None, txid="neo4j-transaction-121", database="neo4j"):
    return {"transactionId": txid, "database": database,
            "metaData": target().metadata if meta is None else meta}


def present():
    return capture_active(target(), observation(1, [row()]))


def test_exact_active_transaction_is_observed_but_cannot_release():
    t = present()
    assert t.transaction_id == "neo4j-transaction-121"
    assert t.signer_authorized is False
    assert t.release_authorized is False
    assert not hasattr(t, "signature")
    assert not hasattr(t, "signed_receipt")


def test_two_absences_are_observation_only_not_authority_to_close():
    result = observe_absence_only(present(), observation(2), observation(3))
    assert result.observed_sequences == (2, 3)
    assert result.signer_authorized is False
    assert result.release_authorized is False
    assert not hasattr(result, "receipt")


@pytest.mark.parametrize("changed", [
    {"assistx_term": 2},
    {"assistx_reservation_id": "c"*32},
    {"assistx_plan_id": "get_task_claim_guard"},
    {"assistx_attempt_id": "different-attempt"},
])
def test_forged_or_changed_metadata_cannot_be_observed(changed):
    t = target()
    meta = dict(t.metadata)
    meta.update(changed)
    with pytest.raises(ObservationRejected,
                       match="MISSING_OR_AMBIGUOUS_ORIGINAL_TRANSACTION"):
        capture_active(t, observation(1, [row(meta)]))


def test_zero_or_two_exact_server_rows_must_be_rejected():
    t = target()
    with pytest.raises(ObservationRejected, match="MISSING_OR_AMBIGUOUS"):
        capture_active(t, observation(1))
    with pytest.raises(ObservationRejected, match="MISSING_OR_AMBIGUOUS"):
        capture_active(t, observation(1, [
            row(txid="neo4j-transaction-1"),
            row(txid="neo4j-transaction-2"),
        ]))


@pytest.mark.parametrize("broken", [
    {"observer_principal": "fixture-broker"},
    {"observer_principal": ""},
    {"member": "other-member"},
    {"boot": "different-boot"},
    {"read_complete": False},
])
def test_observer_spoof_reboot_or_partial_read_rejected(broken):
    snap = replace(observation(1, [row()]), **broken)
    with pytest.raises(ObservationRejected):
        capture_active(target(), snap)


def test_incomplete_show_transactions_rows_rejected():
    with pytest.raises(ObservationRejected, match="INCOMPLETE"):
        capture_active(target(), observation(1, [
            {"transactionId": "neo4j-transaction-1", "database": "neo4j"}]))


@pytest.mark.parametrize("metadata", [
    {},
    {"assistx_term": 0},
    {"assistx_term": True},
    {"assistx_schema": "unverified"},
    {"assistx_reservation_id": "other"},
    {"another_unreviewed_claim": "x"},
])
def test_missing_malformed_or_extra_required_metadata_denied(metadata):
    t = target()
    m = dict(t.metadata)
    m.update(metadata)
    if metadata == {}:
        m.pop("assistx_attempt_id")
    if metadata == {"assistx_term": 0}:
        m["assistx_term"] = 0
    with pytest.raises(ObservationRejected):
        PinnedGraphTarget(t.member, t.boot, t.database,
                          t.reservation_id, m, t.broker_principal)


def test_ambiguous_transaction_reuse_after_initial_observation_rejected():
    t = present()
    with pytest.raises(ObservationRejected,
                       match="EXACT_METADATA_STILL_ACTIVE_OR_REUSED"):
        observe_absence_only(t, observation(2, [
            row(txid="neo4j-transaction-999")]), observation(3))


def test_original_id_present_cannot_sign_closure_even_if_metadata_changed():
    t = present()
    with pytest.raises(ObservationRejected,
                       match="ORIGINAL_TRANSACTION_STILL_VISIBLE"):
        observe_absence_only(t, observation(2, [
            row(meta={"unrelated": True})]), observation(3))


def test_absence_across_generation_change_rejected():
    with pytest.raises(ObservationRejected, match="GENERATION_CHANGED"):
        observe_absence_only(present(), observation(2),
                             replace(observation(3), boot="restarted"))


def test_absence_across_member_change_rejected():
    with pytest.raises(ObservationRejected, match="GENERATION_CHANGED"):
        observe_absence_only(present(), observation(2),
                             replace(observation(3), member="new-primary"))


def test_absence_observed_by_different_principals_rejected():
    with pytest.raises(ObservationRejected, match="INVALID_OBSERVATION_SEQUENCE"):
        observe_absence_only(present(), observation(2),
                             replace(observation(3),
                                     observer_principal="other-observer"))


@pytest.mark.parametrize("bad_sequence", [(1, 2), (3, 2), (2, 2)])
def test_old_duplicate_or_reordered_observations_rejected(bad_sequence):
    with pytest.raises(ObservationRejected, match="INVALID_OBSERVATION_SEQUENCE"):
        observe_absence_only(present(), observation(bad_sequence[0]),
                             observation(bad_sequence[1]))


def test_boolean_sequence_is_not_a_monotonic_counter():
    with pytest.raises(ObservationRejected, match="BAD_OBSERVER_SNAPSHOT"):
        observation(True)


def test_exact_metadata_contract_is_not_open_ended():
    assert META_KEYS == {
        "assistx_schema", "assistx_operation_id", "assistx_attempt_id",
        "assistx_reservation_id", "assistx_epoch", "assistx_term",
        "assistx_plan_id"}


def test_pinned_metadata_is_not_mutable_after_custody():
    source = dict(target().metadata)
    pinned = PinnedGraphTarget(
        "disposable-member-0", "disposable-boot-0",
        "neo4j", "b"*32, source, "fixture-broker")
    source["assistx_term"] = 999
    assert pinned.metadata["assistx_term"] == 1
    with pytest.raises(TypeError):
        pinned.metadata["assistx_term"] = 999
    assert capture_active(pinned, observation(1, [row()])).transaction_id


def test_observer_snapshot_cannot_be_mutated_by_upstream_driver():
    original = row()
    snap = observation(1, [original])
    original["transactionId"] = "neo4j-transaction-999"
    original["metaData"]["assistx_term"] = 999
    assert snap.rows[0]["transactionId"] == "neo4j-transaction-121"
    assert snap.rows[0]["metaData"]["assistx_term"] == 1
    with pytest.raises(TypeError):
        snap.rows[0]["metaData"]["assistx_term"] = 10
    with pytest.raises(TypeError):
        snap.rows[0]["transactionId"] = "neo4j-transaction-999"
    assert capture_active(target(), snap).transaction_id == (
        "neo4j-transaction-121")


def test_changed_snapshot_copy_cannot_rewrite_absence_sequence():
    first = observation(2, [])
    second = observation(3, [])
    assert observe_absence_only(present(), first, second).release_authorized is False
    with pytest.raises(AttributeError):
        second.sequence = 2
