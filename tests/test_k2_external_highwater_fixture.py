"""Disposable dual-ledger rollback tests; NOT independently custodied."""
from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from k2_challenge_spend_fixture import ChallengeHold
from k2_external_highwater_fixture import SyntheticAnchorHold, SyntheticExternalHighwater
from test_k2_challenge_spend_fixture import context as _base_context
from test_k2_challenge_spend_fixture import issue, signed, spend


@pytest.fixture
def signed_context(tmp_path, monkeypatch):
    # Reuse only the synthetic fixture implementation, without calling
    # another pytest fixture object directly or loading project conftest.
    return _base_context.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def anchored(signed_context, tmp_path):
    ledger, raw, nonce, verify = signed_context
    assert ledger.path.parent == tmp_path
    anchor = SyntheticExternalHighwater.bootstrap_new(
        tmp_path / "separate-fake-monotonic-anchor.sqlite"
    )
    issue(ledger, nonce)
    return ledger, anchor, raw, nonce, verify


def anchored_spend(anchored, *, callback=None, pin_override=None):
    ledger, anchor, raw, nonce, verify = anchored
    return ledger.consume_after_verification(
        raw, expected_nonce=nonce, now_ms=signed.NOW,
        verify_signed=verify,
        synthetic_external_reservation=anchor.reserve if callback is None else callback,
        synthetic_anchor_public_key_sha256=(
            anchor.test_public_key_sha256 if pin_override is None else pin_override
        ),
    )


def snapshot(source: Path, backup: Path) -> None:
    with sqlite3.connect(source) as original:
        with sqlite3.connect(backup) as destination:
            original.backup(destination)


def restore(backup: Path, destination: Path) -> None:
    with sqlite3.connect(backup) as original:
        with sqlite3.connect(destination) as current:
            original.backup(current)


def test_anchored_success_still_never_authorizes_dispatch(anchored):
    ledger, anchor, *_ = anchored
    outcome = anchored_spend(anchored)
    assert outcome["sequence"] == 1
    assert outcome["synthetic_external_anchor_reserved"] is True
    assert outcome["independent_custody_verified"] is False
    assert outcome["operator_approval_verified"] is False
    assert outcome["can_dispatch"] is False
    assert outcome["decision"] == "HOLD"
    assert ledger.audit_counts()["highwater"] == 1
    assert anchor.audit() == {"highwater": 1, "spends": 1}


def test_unanchored_sqlite_snapshot_rollback_reuses_nonce(signed_context, tmp_path):
    """Demonstrates the old, stand-alone fixture has no rollback protection."""
    ledger, _, nonce, _ = signed_context
    issue(ledger, nonce)
    before = tmp_path / "before-spend.sqlite"
    snapshot(ledger.path, before)
    assert spend(signed_context)["sequence"] == 1
    restore(before, ledger.path)
    # This WOULD be a replay if independently trusted for real workloads;
    # its result still says can_dispatch=false, so no authority is granted.
    after = spend(signed_context)
    assert after["sequence"] == 1
    assert after["can_dispatch"] is False


def test_rollback_of_only_local_ledger_detected_by_separate_anchor(anchored, tmp_path):
    ledger, anchor, *_ = anchored
    before = tmp_path / "local-before-spend.sqlite"
    snapshot(ledger.path, before)
    assert anchored_spend(anchored)["sequence"] == 1
    restore(before, ledger.path)
    assert ledger.audit_counts()["highwater"] == 0
    assert anchor.audit()["highwater"] == 1
    with pytest.raises(SyntheticAnchorHold, match="external_highwater_rollback_or_fork"):
        anchored_spend(anchored)
    assert ledger.audit_counts()["spent"] == 0
    assert anchor.audit()["spends"] == 1


def test_anchor_reservation_commit_but_lost_acknowledgement_is_hold(anchored):
    ledger, anchor, *_ = anchored

    def reserve_then_lose_ack(nonce, digest, previous):
        anchor.reserve(nonce, digest, previous)
        raise TimeoutError("synthetic lost CAS acknowledgement")

    with pytest.raises(TimeoutError, match="lost CAS"):
        anchored_spend(anchored, callback=reserve_then_lose_ack)
    assert ledger.audit_counts()["highwater"] == 0
    assert anchor.audit() == {"highwater": 1, "spends": 1}
    # Do NOT automatically retry after an unknown external commit outcome.
    with pytest.raises(SyntheticAnchorHold, match="external_highwater_rollback_or_fork"):
        anchored_spend(anchored)
    assert ledger.audit_counts()["spent"] == 0


def test_anchor_commit_but_corrupted_ack_refuses_local_spend(anchored):
    ledger, anchor, *_ = anchored

    def forged_ack(nonce, digest, previous):
        result = anchor.reserve(nonce, digest, previous)
        result["sequence"] += 1
        return result

    with pytest.raises(ChallengeHold, match="synthetic_external_anchor_evidence_invalid"):
        anchored_spend(anchored, callback=forged_ack)
    assert anchor.audit()["highwater"] == 1
    assert ledger.audit_counts()["spent"] == 0


def test_two_databases_competing_consumers_exactly_one_winner(anchored):
    ledger, anchor, *_ = anchored

    def attempt(_):
        try:
            anchored_spend(anchored)
            return "spent"
        except (ChallengeHold, SyntheticAnchorHold) as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(attempt, range(8)))
    assert outcomes.count("spent") == 1
    assert outcomes.count("synthetic_challenge_replayed") == 7
    assert ledger.audit_counts() == {"issued": 1, "spent": 1, "highwater": 1}
    assert anchor.audit() == {"highwater": 1, "spends": 1}


def test_independent_anchor_wrong_previous_highwater_is_denied(anchored):
    ledger, anchor, *_ = anchored
    assert anchor.audit()["highwater"] == 0
    with pytest.raises(SyntheticAnchorHold, match="external_highwater_rollback_or_fork"):
        anchor.reserve("f" * 64, "a" * 64, expected_sequence=1)
    assert ledger.audit_counts()["spent"] == 0


def test_external_store_rollback_also_causes_hold_for_future_sequence(anchored, tmp_path):
    ledger, anchor, raw, nonce, verify = anchored
    external_snapshot = tmp_path / "anchor-before-spend.sqlite"
    snapshot(anchor.path, external_snapshot)
    assert anchored_spend(anchored)["sequence"] == 1
    restore(external_snapshot, anchor.path)
    assert anchor.audit()["highwater"] == 0
    # Two stores have diverged: a subsequent new signed nonce cannot
    # create an anchored commit at the rolled-back sequence.
    with pytest.raises(SyntheticAnchorHold, match="external_highwater_rollback_or_fork"):
        anchor.reserve("d" * 64, "a" * 64, expected_sequence=1)
    assert ledger.audit_counts()["highwater"] == 1


def test_rollback_of_both_test_databases_demonstrates_common_custody_limit(
    anchored, tmp_path
):
    ledger, anchor, *_ = anchored
    lbackup = tmp_path / "local-pre.sqlite"
    abackup = tmp_path / "external-pre.sqlite"
    snapshot(ledger.path, lbackup)
    snapshot(anchor.path, abackup)
    assert anchored_spend(anchored)["sequence"] == 1
    restore(lbackup, ledger.path)
    restore(abackup, anchor.path)
    # This is intentionally the attack demonstration: two locally
    # administered DBs are not truly independent or rollback-resistant.
    repeat = anchored_spend(anchored)
    assert repeat["sequence"] == 1
    assert repeat["independent_custody_verified"] is False
    assert repeat["can_dispatch"] is False


def test_external_anchor_symlink_and_existing_file_are_denied(anchored, tmp_path):
    _, anchor, *_ = anchored
    with pytest.raises(SyntheticAnchorHold, match="anchor_file_exists_or_invalid"):
        SyntheticExternalHighwater.bootstrap_new(anchor.path)
    alias = tmp_path / "anchor-symlink.sqlite"
    alias.symlink_to(anchor.path)
    with pytest.raises(SyntheticAnchorHold, match="anchor_missing_or_symlink"):
        SyntheticExternalHighwater(alias).audit()
    with pytest.raises(SyntheticAnchorHold, match="anchor_file_exists_or_invalid"):
        SyntheticExternalHighwater.bootstrap_new(alias)


def test_invalid_or_duplicate_anchor_spend_denied(anchored):
    ledger, anchor, raw, nonce, verify = anchored
    with pytest.raises(SyntheticAnchorHold, match="anchor_input_invalid"):
        anchor.reserve(nonce, "fake-digest", 0)
    assert anchored_spend(anchored)["sequence"] == 1
    with pytest.raises(SyntheticAnchorHold, match="external_highwater_rollback_or_fork"):
        anchor.reserve(nonce, "a" * 64, 0)
    assert ledger.audit_counts()["highwater"] == 1


def test_missing_highwater_in_separate_anchor_is_hold(anchored):
    ledger, anchor, *_ = anchored
    with sqlite3.connect(anchor.path) as db:
        db.execute("DELETE FROM monotonic")
    with pytest.raises(SyntheticAnchorHold, match="anchor_highwater_missing"):
        anchored_spend(anchored)
    assert ledger.audit_counts()["spent"] == 0



def test_missing_external_anchor_key_pin_blocks_before_any_reservation(anchored):
    ledger, anchor, *_ = anchored
    with pytest.raises(ChallengeHold, match="synthetic_external_anchor_key_not_pinned"):
        anchored_spend(anchored, pin_override="")
    assert anchor.audit()["highwater"] == 0
    assert ledger.audit_counts()["spent"] == 0


def test_wrong_external_anchor_signer_pin_blocks_spend(anchored):
    ledger, anchor, *_ = anchored
    with pytest.raises(ChallengeHold, match="synthetic_external_anchor_signer_not_pinned"):
        anchored_spend(anchored, pin_override="f" * 64)
    # Anchor might have reserved, but must not be treated as approval.
    assert anchor.audit()["highwater"] == 1
    assert ledger.audit_counts()["spent"] == 0


def test_signed_external_ack_tampering_refused_even_when_fields_match(anchored):
    ledger, anchor, *_ = anchored

    def mutate_signed_ack(nonce, digest, previous):
        result = anchor.reserve(nonce, digest, previous)
        # Preserve every claim except the signature.
        sig = result["test_signature_b64"]
        result["test_signature_b64"] = ("A" if sig[0] != "A" else "B") + sig[1:]
        return result

    with pytest.raises(ChallengeHold, match="synthetic_external_anchor_signature_invalid"):
        anchored_spend(anchored, callback=mutate_signed_ack)
    assert anchor.audit()["highwater"] == 1
    assert ledger.audit_counts()["spent"] == 0


def test_missing_signed_external_ack_refuses_locally(anchored):
    ledger, anchor, *_ = anchored

    def drop_signed_ack(nonce, digest, previous):
        response = anchor.reserve(nonce, digest, previous)
        response.pop("test_signature_b64")
        return response

    with pytest.raises(ChallengeHold, match="synthetic_external_anchor_evidence_invalid"):
        anchored_spend(anchored, callback=drop_signed_ack)
    assert ledger.audit_counts()["spent"] == 0


def test_external_test_signature_cannot_change_non_admitting_claims(anchored):
    ledger, anchor, *_ = anchored

    def flip_dispatch(nonce, digest, previous):
        response = anchor.reserve(nonce, digest, previous)
        response["can_dispatch"] = True
        return response

    with pytest.raises(ChallengeHold, match="synthetic_external_anchor_evidence_invalid"):
        anchored_spend(anchored, callback=flip_dispatch)
    assert ledger.audit_counts()["spent"] == 0
