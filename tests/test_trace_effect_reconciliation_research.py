"""Safety-negative fixtures for purely observational reconciliation research."""
from dataclasses import replace

import pytest

from assistx.trace_effect_reconciliation_research import (
    Classification, Operation, ReceiverReceipt, reconcile,
)

OP = Operation("research-domain", "synthetic-op-1", "synthetic-effect-1", "x1-370", 1)


def receipt(status="applied", **changes):
    row = ReceiverReceipt(
        domain=OP.domain, operation_id=OP.operation_id,
        effect_id=OP.effect_id, owner=OP.owner, term=OP.term,
        receipt_id="synthetic-receipt-1", status=status,
        durable=True, boundary_term=1,
        terminal=status in ("applied", "aborted", "rejected-stale"),
    )
    return replace(row, **changes)


def assert_frozen(decision):
    assert decision.automatic_replay_allowed is False
    assert decision.takeover_allowed is False


def test_ack_lost_but_durable_effect_is_confirmed_without_replay():
    d = reconcile(OP, [receipt("unknown", receipt_id="probe"), receipt()])
    assert d.classification is Classification.APPLIED
    assert_frozen(d)


def test_applied_before_later_term_rotation_is_not_stale():
    # The current witness may be term 2, but receiver atomically accepted
    # term 1 while it was still current. A later takeover cannot rewrite time.
    d = reconcile(OP, [receipt(boundary_term=1)])
    assert d.classification is Classification.APPLIED
    assert_frozen(d)


def test_old_owner_applied_after_boundary_advanced_is_a_violation():
    d = reconcile(OP, [receipt(boundary_term=2)])
    assert d.classification is Classification.STALE_APPLIED
    assert_frozen(d)


@pytest.mark.parametrize("evidence", [
    [], [receipt("unknown")], [receipt("not-seen", terminal=False)],
    [receipt("aborted", terminal=False)],
])
def test_no_definitive_atomic_record_remains_unknown(evidence):
    d = reconcile(OP, evidence)
    assert d.classification is Classification.UNKNOWN
    assert_frozen(d)


@pytest.mark.parametrize("status", ["aborted", "rejected-stale"])
def test_terminal_durable_no_effect_is_not_retry_permission(status):
    d = reconcile(OP, [receipt(status)])
    assert d.classification is Classification.ABORTED
    assert_frozen(d)


def test_missing_witness_cannot_be_inferred_from_receiver_absence():
    # No witness is queried here: a not-seen response is not global absence
    # evidence and cannot authorize takeover or automatic replay.
    d = reconcile(OP, [receipt("not-seen")])
    assert d.classification is Classification.UNKNOWN
    assert_frozen(d)


def test_duplicate_copies_of_one_signed_in_future_receipt_are_one_effect():
    r = receipt()
    d = reconcile(OP, [r, r])
    assert d.classification is Classification.APPLIED
    assert_frozen(d)


def test_distinct_applied_receipts_are_replay_alarm():
    d = reconcile(OP, [receipt(), receipt(receipt_id="second")])
    assert d.classification is Classification.CONFLICT
    assert_frozen(d)


def test_applied_and_terminal_rejected_are_conflicting_evidence():
    d = reconcile(OP, [receipt(), receipt("aborted", receipt_id="abort")])
    assert d.classification is Classification.CONFLICT
    assert_frozen(d)


def test_receipt_id_reuse_with_different_content_is_conflicting_evidence():
    d = reconcile(OP, [receipt(), receipt("aborted")])
    assert d.classification is Classification.CONFLICT
    assert_frozen(d)


@pytest.mark.parametrize("changes", [
    {"domain": "wrong-domain"}, {"operation_id": "other-op"},
    {"effect_id": "other-effect"}, {"owner": "xwing"}, {"term": 2},
])
def test_mismatched_owner_term_operation_effect_binding_denied(changes):
    d = reconcile(OP, [receipt(**changes)])
    assert d.classification is Classification.CONFLICT
    assert_frozen(d)


@pytest.mark.parametrize("changes", [
    {"durable": False}, {"boundary_term": None},
    {"boundary_term": 0}, {"boundary_term": True},
    {"terminal": True, "status": "unknown"},
    {"status": "invented"}, {"receipt_id": ""},
    {"status": "applied", "terminal": False},
])
def test_untrusted_or_incomplete_receiver_record_fails_closed(changes):
    d = reconcile(OP, [receipt(**changes)])
    assert d.classification is Classification.UNTRUSTED
    assert_frozen(d)


def test_invalid_operation_identity_cannot_be_reconciled():
    assert reconcile(replace(OP, term=0), [receipt()]).classification is Classification.UNTRUSTED
    assert reconcile(replace(OP, term=True), [receipt()]).classification is Classification.UNTRUSTED


def test_sequential_term_rotate_cannot_convert_old_unknown_to_replay():
    old = reconcile(OP, [receipt("unknown")])
    new = reconcile(replace(OP, owner="xwing", term=2), [])
    assert old.classification is Classification.UNKNOWN
    assert new.classification is Classification.UNKNOWN
    assert_frozen(old)
    assert_frozen(new)
