"""Strict read-only verification of a previously COMMITTED synthetic outbox.

All roots and evidence are controlled by ONE pytest administrator; this is a
failure-detection experiment, never independent custody or release.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/research-verify-recovered-effect-outbox.py"
SPEC = importlib.util.spec_from_file_location("verified_outbox_reader", SOURCE)
assert SPEC and SPEC.loader
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)

PARENT = ROOT / "tests/test_research_atomic_effect_bundle_outbox.py"
SPEC2 = importlib.util.spec_from_file_location("atomic_outbox_previous_test", PARENT)
assert SPEC2 and SPEC2.loader
previous = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(previous)


@pytest.fixture
def lab(tmp_path):
    state = previous.state.__wrapped__(tmp_path)
    pinned_sha = hashlib.sha256(
        previous.module._encoded(state["bundle"]).encode("ascii")
    ).hexdigest()

    def inspect(*, bundle_sha=pinned_sha, prev_hash=None,
                effect_root=None, witness_root=None, clock=previous.NOW):
        return reader.inspect(
            state["outbox_member"],
            witness_id=previous.WID, seq=12,
            resource=state["resource"], anchor=state["anchor"],
            artifact=state["artifact"],
            expected=state["expected"],
            pinned_bundle_sha256=bundle_sha,
            pinned_previous_head_hash=(
                state["baseline"]["head_hash"]
                if prev_hash is None else prev_hash
            ),
            pinned_effect_root=(
                state["effect_root"] if effect_root is None else effect_root
            ),
            pinned_witness_root=(
                state["witness_root"] if witness_root is None else witness_root
            ),
            challenge=previous.CHALLENGE,
            now_ms=clock,
        )

    return {**state, "pin": pinned_sha, "inspect": inspect}


def assert_denied(result, expected):
    assert result["verdict"] == expected
    assert result["retry_authorized"] is False
    assert result["release_authorized"] is False
    assert result["schema_version"] == 1


def replace_bundle_sql(lab, mutate):
    with sqlite3.connect(lab["outbox_member"]) as admin:
        row = admin.execute(
            "SELECT bundle_json FROM signed_bundle_outbox"
        ).fetchone()
        bundle = json.loads(row[0])
        mutate(bundle)
        encoded = previous.module._encoded(bundle)
        digest = hashlib.sha256(encoded.encode("ascii")).hexdigest()
        admin.execute(
            "UPDATE signed_bundle_outbox SET bundle_json=?,bundle_sha256=?",
            (encoded, digest),
        )
    return digest


def test_readonly_recovery_after_real_postcommit_child_death(lab):
    child = lab["child"]("after_commit_before_ack")
    assert child.returncode == -previous.signal.SIGKILL
    assert_denied(
        lab["inspect"](),
        "RECOVERY_CROSS_SIGNED_BUNDLE_OBSERVED_HOLD",
    )


def test_before_commit_child_death_never_grants_retry(lab):
    child = lab["child"]("after_outbox_insert_before_commit")
    assert child.returncode == -previous.signal.SIGKILL
    assert_denied(lab["inspect"](), "OUTBOX_NOT_COMMITTED_HOLD")


def test_normal_commit_readonly_recovery_returns_only_hold(lab):
    assert lab["attempt"]()[0] is True
    result = lab["inspect"]()
    assert_denied(result, "RECOVERY_CROSS_SIGNED_BUNDLE_OBSERVED_HOLD")
    assert "bundle" not in result  # verifier never issues an execution token


def test_original_outbox_local_selfhash_cannot_detect_validly_rehashed_tamper(lab):
    assert lab["attempt"]()[0] is True
    new_hash = replace_bundle_sql(
        lab, lambda b: b["witness_binding"].update(signature="AA=="),
    )
    assert new_hash != lab["pin"]
    # #396 only compares local bundle_sha256 against local JSON; it will
    # happily call this complete even with a forged binding signature.
    assert lab["fresh"]()["verdict"] == "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"
    assert_denied(
        lab["inspect"](), "RECOVERY_PINNED_BUNDLE_DIGEST_CONFLICT_NO_GO",
    )


def test_forged_selfconsistent_hash_and_caller_changed_pin_still_denied(lab):
    assert lab["attempt"]()[0] is True
    attacker_digest = replace_bundle_sql(
        lab, lambda b: b["witness_binding"].update(signature="AA=="),
    )
    # Supplying the forged digest cannot bypass independent Ed25519 roots.
    assert_denied(
        lab["inspect"](bundle_sha=attacker_digest),
        "RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO",
    )


def test_original_outbox_local_signature_mutation_is_detected(lab):
    assert lab["attempt"]()[0] is True
    new_hash = replace_bundle_sql(
        lab, lambda b: b["effect_attestation"].update(signature="AA=="),
    )
    assert lab["fresh"]()["verdict"] == "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD"
    assert_denied(
        lab["inspect"](bundle_sha=new_hash),
        "RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO",
    )


def test_member_certificate_signature_changed_but_issued_row_unchanged_denied(lab):
    assert lab["attempt"]()[0] is True
    new_sha = replace_bundle_sql(
        lab, lambda b: b["witness_certificate"].update(signature="AA=="),
    )
    # In this case even #396's basic issued-record signature check denies.
    assert lab["fresh"]()["verdict"] == "OUTBOX_CERTIFICATE_CONTRADICTION_NO_GO"
    assert_denied(
        lab["inspect"](bundle_sha=new_sha),
        "OUTBOX_CERTIFICATE_CONTRADICTION_NO_GO",
    )


def test_broken_effect_bytes_after_witness_commit_denied(lab):
    assert lab["attempt"]()[0] is True
    lab["artifact"].write_bytes(b"late-mutation")
    assert_denied(
        lab["inspect"](), "RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO",
    )


def test_source_effect_mutated_after_witness_commit_denied(lab):
    assert lab["attempt"]()[0] is True
    with sqlite3.connect(lab["resource"]) as db:
        db.execute("UPDATE effect SET generation=31337")
    assert_denied(
        lab["inspect"](), "RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO",
    )


def test_anchor_consumption_revoked_after_commit_denied(lab):
    assert lab["attempt"]()[0] is True
    with sqlite3.connect(lab["anchor"]) as db:
        db.execute("DELETE FROM consumed_receipts")
    assert_denied(
        lab["inspect"](), "RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO",
    )


def test_wrong_effect_signer_root_denied(lab):
    assert lab["attempt"]()[0] is True
    assert_denied(
        lab["inspect"](effect_root=b"x" * 32),
        "RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO",
    )


def test_wrong_witness_signer_root_denied(lab):
    assert lab["attempt"]()[0] is True
    assert_denied(
        lab["inspect"](witness_root=b"y" * 32),
        "RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO",
    )


def test_expired_full_bundle_cannot_be_renewed_automatically(lab):
    assert lab["attempt"]()[0] is True
    assert_denied(
        lab["inspect"](clock=previous.NOW + 20_000),
        "RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO",
    )


def test_external_bundle_pin_missing_fails_closed(lab):
    assert lab["attempt"]()[0] is True
    assert_denied(
        lab["inspect"](bundle_sha="0" * 63),
        "INDEPENDENT_RECOVERY_PINS_MISSING_NO_GO",
    )


def test_external_previous_tip_pin_mismatch_denied(lab):
    assert lab["attempt"]()[0] is True
    assert_denied(
        lab["inspect"](prev_hash="1" * 64),
        "RECOVERY_PINNED_HEAD_OR_RECEIPT_CONFLICT_NO_GO",
    )


def test_whole_witness_db_restore_still_looks_uncommitted_no_go(lab, tmp_path):
    snap = tmp_path / "prior-member-state.sqlite"
    shutil.copy2(lab["outbox_member"], snap)
    assert lab["attempt"]()[0] is True
    shutil.copy2(snap, lab["outbox_member"])
    assert_denied(lab["inspect"](), "OUTBOX_NOT_COMMITTED_HOLD")
    # This is a replay/restore counterexample even with a real previous
    # signed bundle hash in caller context: no globally governed ledger tip.


def test_missing_witness_outbox_does_not_create_new_file(lab):
    lab["outbox_member"].unlink()
    assert_denied(lab["inspect"](), "OUTBOX_SCOPE_INVALID_NO_GO")
    assert not lab["outbox_member"].exists()


def test_partial_witness_outbox_records_never_authenticated(lab):
    assert lab["attempt"]()[0] is True
    with sqlite3.connect(lab["outbox_member"]) as db:
        db.execute("DELETE FROM signed_bundle_outbox")
    assert_denied(lab["inspect"](), "OUTBOX_LEDGER_CONTRADICTION_NO_GO")


def test_two_outbox_reads_that_disagree_are_denied(lab, monkeypatch):
    assert lab["attempt"]()[0] is True
    outbox = reader._source("research-atomic-effect-bundle-outbox.py")
    original = outbox.readback
    calls = 0

    def altered_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            with sqlite3.connect(lab["outbox_member"]) as db:
                db.execute("DELETE FROM signed_bundle_outbox")
        return original(*args, **kwargs)

    monkeypatch.setattr(outbox, "readback", altered_second)
    assert_denied(
        lab["inspect"](), "RECOVERY_OUTBOX_CHANGED_DURING_READBACK_NO_GO",
    )
    assert calls == 2


def test_same_admin_controls_all_pins_and_origins_no_independence(lab):
    assert lab["attempt"]()[0] is True
    # All test roots and the very own expected hash are constructed by
    # the same fixture. This accepts coherent synthetic inputs, not
    # a real resource owner or separately administered latest tip.
    assert_denied(
        lab["inspect"](), "RECOVERY_CROSS_SIGNED_BUNDLE_OBSERVED_HOLD",
    )


def test_verifier_has_no_signing_private_key_or_mutation_entrypoint():
    code = SOURCE.read_text()
    assert "Ed25519PrivateKey" not in code
    assert "RESEARCH_ONLY_NO_RECOVERY_DISPATCH_OR_RELEASE_ENTRYPOINT" in code
    assert "retry_authorized" in code and "release_authorized" in code
    assert "INSERT INTO " not in code and "UPDATE " not in code
