"""Actual fsynced disposable effect vs privileged SQL false-commit fixture.

No real resource/device; all fixture databases, keys and artifacts are
under the SAME pytest owner. Counterexamples are intentional security NO-GOs.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/research-effect-artifact-gate.py"
SPEC = importlib.util.spec_from_file_location("synthetic_effect_gate", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
PREV = ROOT / "tests/test_research_durable_witness_issuance.py"
SPEC2 = importlib.util.spec_from_file_location("synthetic_effect_prev_tests", PREV)
assert SPEC2 and SPEC2.loader
prior = importlib.util.module_from_spec(SPEC2)
SPEC2.loader.exec_module(prior)
WID = prior.IDS[0]
PAYLOAD = b"actual-fsynced-synthetic-side-effect-v1\n"

EXPECTED_DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture
def lab(tmp_path):
    data = prior.lab.__wrapped__(tmp_path)
    artifact = tmp_path / "synthetic-resource-effect-v1.bin"

    def issue(*, sha=EXPECTED_DIGEST, path=artifact):
        return gate.issue_with_effect(
            data["paths"][WID],
            witness_id=WID, effect_path=path,
            pinned_effect_sha256=sha,
            tip=data["new_tip"], expected=data["expected"],
            resource=data["resource"], anchor=data["anchor"],
            signer=data["keys"][WID], key_epoch=3,
            challenge=prior.old.CHALLENGE, now_ms=prior.old.NOW,
        )

    return {**data, "artifact": artifact, "issue_effect": issue}


def fake_sql_anchor_commit_without_authorized_cas(lab):
    # Real disposable source COMMIT, but no authorized anchor decision.
    lab["crash"]("source_after_commit_before_anchor")
    expected = lab["expected"]
    with sqlite3.connect(lab["anchor"]) as admin:
        admin.execute(
            "UPDATE highwater SET commit_seq=12,last_nonce=?",
            (expected["nonce"],),
        )
        admin.execute(
            "INSERT INTO spent_challenges VALUES (?,?,?,?)",
            (expected["challenge"], expected["receipt_digest"], 4, 12),
        )
        admin.execute(
            "INSERT INTO consumed_receipts VALUES (?,?,?,?)",
            (expected["receipt_digest"], expected["challenge"], 4, 12),
        )


def test_fsync_artifact_and_real_source_anchor_readback_allows_research_only(lab):
    lab["crash"]("no_crash")
    assert gate.create_disposable_effect(lab["artifact"], PAYLOAD) == EXPECTED_DIGEST
    ok, reason, cert = lab["issue_effect"]()
    assert (ok, reason) == (True, "RESEARCH_WITNESS_LOCALLY_ISSUED_HOLD")
    assert cert["tip"]["commit_seq"] == 12


def test_forged_sql_without_durable_artifact_cannot_issue(lab):
    fake_sql_anchor_commit_without_authorized_cas(lab)
    assert lab["issue_effect"]() == (
        False, "EFFECT_ARTIFACT_ABSENT_OR_UNAVAILABLE", None
    )
    assert gate._issuer().read_tip(
        lab["paths"][WID], witness_id=WID
    )["commit_seq"] == 11


def test_forged_sql_plus_same_host_artifact_can_still_issue_no_go(lab):
    fake_sql_anchor_commit_without_authorized_cas(lab)
    # Shared administrator controls SQL, artifact contents, and caller pin.
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    assert lab["issue_effect"]()[:2] == (
        True, "RESEARCH_WITNESS_LOCALLY_ISSUED_HOLD"
    )


def test_write_once_effect_creation_refuses_overwrite(lab):
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    with pytest.raises(ValueError, match="creation_denied"):
        gate.create_disposable_effect(lab["artifact"], b"new content")
    assert lab["artifact"].read_bytes() == PAYLOAD


def test_wrong_effect_digest_fails_closed_without_signature(lab):
    lab["crash"]("no_crash")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    assert lab["issue_effect"](sha="0" * 64) == (
        False, "EFFECT_ARTIFACT_IDENTITY_OR_CONTENT_MISMATCH", None
    )
    assert gate._issuer().read_tip(
        lab["paths"][WID], witness_id=WID
    )["commit_seq"] == 11


def test_file_mutation_after_fsync_denied(lab):
    lab["crash"]("no_crash")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    lab["artifact"].write_bytes(b"tampered after fsync")
    assert lab["issue_effect"]() == (
        False, "EFFECT_ARTIFACT_IDENTITY_OR_CONTENT_MISMATCH", None
    )


def test_symlinked_artifact_is_not_accepted(lab, tmp_path):
    lab["crash"]("no_crash")
    alternate = tmp_path / "other-file.bin"
    alternate.write_bytes(PAYLOAD)
    lab["artifact"].symlink_to(alternate)
    assert lab["issue_effect"]() == (
        False, "EFFECT_ARTIFACT_CUSTODY_OR_DIGEST_MISSING", None
    )


def test_disposable_artifact_missing_marker_denies(lab):
    lab["crash"]("no_crash")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    (lab["artifact"].parent / ".synthetic-crash-lab-only").unlink()
    assert lab["issue_effect"]() == (
        False, "EFFECT_ARTIFACT_CUSTODY_OR_DIGEST_MISSING", None
    )


def test_artifact_replacement_before_witness_commit_denies_and_rolls_back(
    lab, monkeypatch, tmp_path,
):
    lab["crash"]("no_crash")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    other = tmp_path / "substitute.bin"
    other.write_bytes(PAYLOAD)
    original_issue = gate._issuer().issue

    def replace_during(*args, callback=None, **kwargs):
        assert callback is not None

        def before_signing(stage):
            if stage == "before_witness_commit":
                os.replace(other, lab["artifact"])
            callback(stage)

        return original_issue(*args, callback=before_signing, **kwargs)

    monkeypatch.setattr(gate._issuer(), "issue", replace_during)
    assert lab["issue_effect"]() == (
        False, "EFFECT_CHANGED_BEFORE_COMMIT", None
    )
    assert gate._issuer().read_tip(
        lab["paths"][WID], witness_id=WID
    )["commit_seq"] == 11


def test_artifact_replacement_after_witness_commit_is_uncertain_no_retry(
    lab, monkeypatch, tmp_path,
):
    lab["crash"]("no_crash")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    other = tmp_path / "substitute.bin"
    other.write_bytes(PAYLOAD)
    original_issue = gate._issuer().issue

    def replace_during(*args, callback=None, **kwargs):
        def after_signing(stage):
            if stage == "after_witness_commit_before_ack":
                os.replace(other, lab["artifact"])
            callback(stage)

        return original_issue(*args, callback=after_signing, **kwargs)

    monkeypatch.setattr(gate._issuer(), "issue", replace_during)
    with pytest.raises(gate.ArtifactChangedAfterWitnessCommit, match="NO_RETRY"):
        lab["issue_effect"]()
    assert gate._issuer().read_tip(
        lab["paths"][WID], witness_id=WID
    )["commit_seq"] == 12


def test_post_commit_privileged_artifact_mutation_remains_no_go(lab):
    lab["crash"]("no_crash")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    assert lab["issue_effect"]()[0] is True
    lab["artifact"].write_bytes(b"changed after signed issuance")
    assert lab["artifact"].read_bytes() != PAYLOAD
    # Signature already issued. Same shared OS administrator can rewrite it.
    assert gate._issuer().read_tip(
        lab["paths"][WID], witness_id=WID
    )["commit_seq"] == 12


def test_shared_administrator_can_recreate_matching_artifact_no_go(lab):
    lab["crash"]("no_crash")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    lab["artifact"].unlink()
    # O_EXCL prevents accidental overwrite, NOT host-admin delete/recreate.
    assert gate.create_disposable_effect(lab["artifact"], PAYLOAD) == EXPECTED_DIGEST
    assert lab["issue_effect"]()[0] is True


def test_module_is_import_only_not_production_signer():
    source = SCRIPT.read_text()
    assert "NO_EXTERNAL_EFFECT_OR_RELEASE_ENTRYPOINT" in source
    assert "O_EXCL" in source and "O_NOFOLLOW" in source
    assert "os.fsync" in source
    assert " Ed25519PrivateKey" not in source
    assert "docker" not in source and "kubectl" not in source


def test_effect_file_alone_does_not_certify_anchor_commit(lab):
    lab["crash"]("source_after_commit_before_anchor")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    assert lab["issue_effect"]() == (
        False, "WITNESS_REQUIRES_OBSERVED_ANCHOR_COMMIT", None
    )


def test_wrong_expected_artifact_hash_is_not_authorization(lab):
    lab["crash"]("no_crash")
    gate.create_disposable_effect(lab["artifact"], PAYLOAD)
    assert lab["issue_effect"](sha="not a hash") == (
        False, "EFFECT_ARTIFACT_CUSTODY_OR_DIGEST_MISSING", None
    )


def test_artifact_bootstrap_refuses_empty_payload(lab):
    with pytest.raises(ValueError, match="creation_denied"):
        gate.create_disposable_effect(lab["artifact"], b"")
    assert not lab["artifact"].exists()
