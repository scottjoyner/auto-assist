"""Three synthetic durable witness SQL ledgers, with real competing writers.

All three DBs and all signing keys remain under ONE pytest administrator;
passing tests do NOT establish independently governed resource custody.
"""
from __future__ import annotations

import concurrent.futures
import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/research-durable-witness-issuance.py"
SPEC = importlib.util.spec_from_file_location("local_witness_issue", SCRIPT)
assert SPEC and SPEC.loader
issuance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issuance)
OLDER = ROOT / "tests/test_research_quorum_highwater_tip.py"
SPEC2 = importlib.util.spec_from_file_location("previous_quorum_tests", OLDER)
assert SPEC2 and SPEC2.loader
old = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(old)
IDS = old.IDS


@pytest.fixture
def lab(tmp_path):
    trial = old.lab.__wrapped__(tmp_path)
    witness_paths = {
        wid: tmp_path / f"synthetic-{wid}.sqlite" for wid in IDS
    }
    for wid, path in witness_paths.items():
        issuance.initialize(
            path, witness_id=wid, baseline=trial["baseline"]
        )

    def issue_one(wid, *, callback=None, tip=None, resource=None, anchor=None):
        return issuance.issue(
            witness_paths[wid], witness_id=wid,
            tip=trial["new_tip"] if tip is None else tip,
            expected=trial["expected"],
            resource=trial["resource"] if resource is None else resource,
            anchor=trial["anchor"] if anchor is None else anchor,
            signer=trial["keys"][wid], key_epoch=3,
            challenge=old.CHALLENGE, now_ms=old.NOW,
            callback=callback,
        )

    return {**trial, "paths": witness_paths, "issue": issue_one}


def test_three_member_ledgers_advance_only_after_real_disposable_anchor_commit(lab):
    lab["crash"]("no_crash")
    certs = []
    for wid in IDS:
        ok, reason, cert = lab["issue"](wid)
        assert (ok, reason) == (True, "RESEARCH_WITNESS_LOCALLY_ISSUED_HOLD")
        certs.append(cert)
        tip = issuance.read_tip(lab["paths"][wid], witness_id=wid)
        assert tip["commit_seq"] == 12
        assert tip["head_hash"] == lab["new_tip"]["head_hash"]
    assert old.verdict(lab, certs) == "ANCHOR_CONSUMED_OBSERVED_HOLD"
    # Separate database files are still under one host administrator.
    assert len({str(p) for p in lab["paths"].values()}) == 3


@pytest.mark.parametrize("stage", [
    "source_pre_commit",
    "source_after_commit_before_anchor",
    "anchor_after_updates_before_commit",
])
def test_witness_cannot_issue_from_source_without_anchor_commit(lab, stage):
    lab["crash"](stage)
    denied = lab["issue"](IDS[0])
    assert denied == (
        False, "WITNESS_REQUIRES_OBSERVED_ANCHOR_COMMIT", None
    )
    assert issuance.read_tip(
        lab["paths"][IDS[0]], witness_id=IDS[0]
    )["commit_seq"] == 11


def test_no_new_witness_file_is_silently_created(lab):
    path = lab["paths"][IDS[0]]
    path.unlink()
    assert lab["issue"](IDS[0]) == (
        False, "WITNESS_DISPOSABLE_SCOPE_DENIED", None
    )
    assert not path.exists()


def test_normal_duplicate_issue_cannot_advance_again(lab):
    lab["crash"]("no_crash")
    assert lab["issue"](IDS[0])[0] is True
    assert lab["issue"](IDS[0]) == (
        False, "WITNESS_STALE_OR_ALREADY_ISSUED", None
    )
    with sqlite3.connect(lab["paths"][IDS[0]]) as db:
        assert db.execute("SELECT count(*) FROM issued").fetchone() == (1,)


def test_two_concurrent_issuers_for_one_witness_have_one_winner(lab):
    lab["crash"]("no_crash")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: lab["issue"](IDS[0]), range(2)))
    assert sum(ok for ok, _, _ in responses) == 1
    assert sum(
        reason == "WITNESS_STALE_OR_ALREADY_ISSUED" for _, reason, _ in responses
    ) == 1
    with sqlite3.connect(lab["paths"][IDS[0]]) as db:
        assert db.execute("SELECT count(*) FROM issued").fetchone() == (1,)


def test_competing_member_instances_can_issue_without_sharing_sql_lock(lab):
    lab["crash"]("no_crash")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lab["issue"], IDS))
    assert len(replies) == 3 and all(ok for ok, _, _ in replies)


def test_malformed_or_fictional_signed_tip_not_issued(lab):
    lab["crash"]("no_crash")
    wrong = dict(lab["new_tip"], receipt_digest="a" * 64)
    wrong["head_hash"] = old.quorum.link(wrong)
    assert lab["issue"](IDS[0], tip=wrong) == (
        False, "WITNESS_EFFECT_DIGEST_MISMATCH", None
    )


def test_missing_expected_source_effect_does_not_sign(lab):
    lab["crash"]("no_crash")
    with sqlite3.connect(lab["resource"]) as db:
        db.execute("DELETE FROM effect")
    assert lab["issue"](IDS[0]) == (
        False, "WITNESS_REQUIRES_OBSERVED_ANCHOR_COMMIT", None
    )


def test_precommit_fault_rolls_back_member_ledger(lab):
    lab["crash"]("no_crash")

    def before(stage):
        if stage == "before_witness_commit":
            raise RuntimeError("synthetic-precommit-error")

    with pytest.raises(RuntimeError, match="precommit"):
        lab["issue"](IDS[0], callback=before)
    assert issuance.read_tip(
        lab["paths"][IDS[0]], witness_id=IDS[0]
    )["commit_seq"] == 11
    assert lab["issue"](IDS[0])[0] is True


def test_aftercommit_ack_loss_observed_without_resigning(lab):
    lab["crash"]("no_crash")

    def after(stage):
        if stage == "after_witness_commit_before_ack":
            raise RuntimeError("synthetic-ack-lost")

    with pytest.raises(issuance.UncertainWitnessCommit, match="NO_RETRY"):
        lab["issue"](IDS[0], callback=after)
    # Caller saw no successful response but new tip is already durable.
    assert issuance.read_tip(
        lab["paths"][IDS[0]], witness_id=IDS[0]
    )["commit_seq"] == 12
    assert lab["issue"](IDS[0]) == (
        False, "WITNESS_STALE_OR_ALREADY_ISSUED", None
    )


def test_protected_member_ledger_detects_anchor_restore(lab, tmp_path):
    snapshot = tmp_path / "old-anchor.sqlite"
    shutil.copy2(lab["anchor"], snapshot)
    lab["crash"]("no_crash")
    certs = [lab["issue"](wid)[2] for wid in IDS[:2]]
    assert old.verdict(lab, certs) == "ANCHOR_CONSUMED_OBSERVED_HOLD"
    shutil.copy2(snapshot, lab["anchor"])
    assert old.verdict(lab, certs) == "QUORUM_DETECTS_LOCAL_ANCHOR_RESTORE_NO_GO"
    for wid in IDS[:2]:
        assert issuance.read_tip(
            lab["paths"][wid], witness_id=wid
        )["commit_seq"] == 12


def test_full_member_restore_reopens_old_checkpoint_no_go(lab, tmp_path):
    saved_anchor = tmp_path / "old-anchor.sqlite"
    shutil.copy2(lab["anchor"], saved_anchor)
    snapshots = {}
    for wid in IDS[:2]:
        snapshot = tmp_path / f"pre-{wid}.sqlite"
        shutil.copy2(lab["paths"][wid], snapshot)
        snapshots[wid] = snapshot
    lab["crash"]("no_crash")
    for wid in IDS[:2]:
        assert lab["issue"](wid)[0] is True
    shutil.copy2(saved_anchor, lab["anchor"])
    for wid, saved in snapshots.items():
        shutil.copy2(saved, lab["paths"][wid])
        assert issuance.read_tip(
            lab["paths"][wid], witness_id=wid
        )["commit_seq"] == 11
    stale = [
        lab["sign"](wid, lab["old_tip"]) for wid in IDS[:2]
    ]
    assert old.verdict(lab, stale) == "SOURCE_COMMITTED_ANCHOR_PENDING_HOLD"


def test_direct_admin_sql_rewinds_witness_tip_and_issued_rows_no_go(lab):
    lab["crash"]("no_crash")
    wid = IDS[0]
    assert lab["issue"](wid)[0] is True
    with sqlite3.connect(lab["paths"][wid]) as admin:
        admin.execute("DELETE FROM issued")
        admin.execute(
            "UPDATE tip SET commit_seq=11,last_nonce=?,receipt_digest=?,head_hash=?",
            (lab["baseline"]["last_nonce"], lab["baseline"]["receipt_digest"],
             lab["baseline"]["head_hash"]),
        )
    assert issuance.read_tip(
        lab["paths"][wid], witness_id=wid
    )["commit_seq"] == 11
    # Even this "monotonic" SQLite witness can now issue the same seq again.
    assert lab["issue"](wid)[0] is True


def test_custody_is_not_separate_just_because_member_keys_are_distinct(lab):
    lab["crash"]("no_crash")
    assert len({lab["roots"][wid]["public"] for wid in IDS}) == 3
    assert all(lab["issue"](wid)[0] for wid in IDS)
    # All synthetic SQL files have the same pytest parent directory.
    assert len({path.parent for path in lab["paths"].values()}) == 1


def test_research_source_has_no_service_or_production_entrypoint():
    source = SCRIPT.read_text()
    assert "NO_SIGNER_SERVICE_OR_PRODUCTION_ENTRYPOINT" in source
    assert "BEGIN IMMEDIATE" in source
    assert "Ed25519PrivateKey" in source
    assert "mode=rw" in source
    assert "docker " not in source and "kubectl " not in source


def test_symlinked_witness_file_denies_without_writes(lab, tmp_path):
    wid = IDS[0]
    original = lab["paths"][wid]
    moved = tmp_path / "original-member-ledger.sqlite"
    original.rename(moved)
    original.symlink_to(moved)
    lab["crash"]("no_crash")
    assert lab["issue"](wid) == (
        False, "WITNESS_DISPOSABLE_SCOPE_DENIED", None
    )


def test_member_missing_anchor_denies_no_silent_creation(lab):
    lab["crash"]("no_crash")
    lab["anchor"].unlink()
    assert lab["issue"](IDS[0]) == (
        False, "WITNESS_LOCAL_RESOURCE_UNAVAILABLE", None
    )
    assert not lab["anchor"].exists()


def test_member_ledger_bootstrap_cannot_overwrite_existing_history(lab):
    wid = IDS[0]
    with pytest.raises(ValueError, match="bootstrap_denied"):
        issuance.initialize(
            lab["paths"][wid], witness_id=wid, baseline=lab["baseline"]
        )


def test_privileged_forged_anchor_rows_are_accepted_as_committed_no_go(lab):
    # A real source COMMIT occurs, but NO authenticated anchor-CAS ever ran.
    lab["crash"]("source_after_commit_before_anchor")
    claimed = lab["expected"]
    # DBA writes self-consistent anchor state directly, bypassing the
    # authorized receipt verifier and atomic consumption entrypoint.
    with sqlite3.connect(lab["anchor"]) as admin:
        admin.execute(
            "UPDATE highwater SET commit_seq=?,last_nonce=?",
            (claimed["commit_seq"], claimed["nonce"]),
        )
        admin.execute(
            "INSERT INTO spent_challenges VALUES (?,?,?,?)",
            (claimed["challenge"], claimed["receipt_digest"],
             claimed["term"], claimed["commit_seq"]),
        )
        admin.execute(
            "INSERT INTO consumed_receipts VALUES (?,?,?,?)",
            (claimed["receipt_digest"], claimed["challenge"],
             claimed["term"], claimed["commit_seq"]),
        )
    # The member's *locally protected* monotonic SQL ledger cannot tell
    # forged DBA table rows from an actual authorized anchor COMMIT.
    ok, reason, cert = lab["issue"](IDS[0])
    assert (ok, reason) == (True, "RESEARCH_WITNESS_LOCALLY_ISSUED_HOLD")
    assert cert["tip"]["commit_seq"] == 12
