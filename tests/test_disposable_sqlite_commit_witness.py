"""Disposable resource transaction + evidence/restore counterexamples.

All database paths are pytest tmp_path. No production credentials, processes,
network, Neo4j, PostgreSQL, router, graph or deployment activity.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "probe-disposable-sqlite-commit.py"
SPEC = importlib.util.spec_from_file_location("disposable_resource_sqlite", SRC)
assert SPEC and SPEC.loader
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)
CHECKSUM = "c" * 64
REVISION = "d" * 64
NONCE = "e" * 32
NOW = 1792000000000


@pytest.fixture
def stores(tmp_path):
    resource = tmp_path / "resource-only.sqlite"
    anchor = tmp_path / "external-anchor-simulated.sqlite"
    lab.initialize(resource, anchor)
    return resource, anchor


def applied(resource, *, lost=False, nonce=NONCE):
    return lab.commit_effect(
        resource, expected_term=1, expected_sequence=1,
        generation=2, checksum=CHECKSUM, revision_sha256=REVISION,
        nonce=nonce, lose_ack_after_commit=lost,
    )


def _load_signed_harness():
    source = ROOT / "scripts" / "prove-independent-convergence-witness.py"
    if not source.is_file():
        pytest.skip("source-verifier #373 is intentionally unmerged")
    spec = importlib.util.spec_from_file_location("convergence_source_witness", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate_signed(resource, anchor, *, callback=None):
    v = _load_signed_harness()
    private = Ed25519PrivateKey.generate()
    state = lab.resource_readback(resource)
    claim = {
        "schema_version": 1, "audience": "convergence-evidence-research",
        "resource_id": state["resource_id"], "generation": state["generation"],
        "checksum": state["checksum"],
        "revision_sha256": state["revision_sha256"],
        "evidence_sha256": "f" * 64, "term": state["term"],
        "commit_seq": state["commit_seq"], "nonce": NONCE,
        "issued_at_ms": NOW - 1000, "expires_at_ms": NOW + 60000,
    }
    roots = {"independent-resource-v1": private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )}
    envelope = {
        "claim": claim, "key_id": "independent-resource-v1",
        "signature": base64.b64encode(private.sign(v._canonical_claim(claim))).decode(),
    }
    expected = {
        k: claim[k] for k in (
            "resource_id", "generation", "checksum",
            "revision_sha256", "evidence_sha256",
        )
    }
    return v.evaluate(
        envelope, trust_roots=roots, expected_evidence=expected,
        external_highwater=lab.anchor_readback(anchor),
        readback=callback or (lambda: lab.resource_readback(resource)),
        now_ms=NOW,
    )


def test_atomic_resource_effect_and_receipt_visible_on_new_read_only_connection(stores):
    resource, anchor = stores
    before = lab.resource_readback(resource)
    assert before["commit_seq"] == 1 and before["committed"] is True
    applied(resource)
    after = lab.resource_readback(resource)
    assert after == {
        "resource_id": "auto-router-runtime-projection",
        "term": 1, "commit_seq": 2, "generation": 2,
        "checksum": CHECKSUM, "revision_sha256": REVISION, "committed": True,
    }
    assert lab.anchor_readback(anchor)["commit_seq"] == 1
    with sqlite3.connect(f"{resource.as_uri()}?mode=ro", uri=True) as view:
        with pytest.raises(sqlite3.OperationalError):
            view.execute("UPDATE resource_state SET commit_seq=900")


def test_independent_process_reads_committed_effect_but_has_no_governance(stores):
    resource, _ = stores
    applied(resource)
    code = (
        "import importlib.util,json,sys; "
        "s=importlib.util.spec_from_file_location('resource_probe',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m); "
        "print(json.dumps(m.resource_readback(__import__('pathlib').Path(sys.argv[2]))))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, str(SRC), str(resource)],
        capture_output=True, text=True, timeout=5, check=True,
    )
    receipt = json.loads(proc.stdout)
    assert receipt["commit_seq"] == 2 and receipt["committed"] is True


def test_wrong_fence_and_repeated_nonce_cannot_commit_a_second_effect(stores):
    resource, _ = stores
    applied(resource)
    before = lab.resource_readback(resource)
    with pytest.raises(lab.RejectedCommit):
        applied(resource)
    with pytest.raises(lab.RejectedCommit):
        lab.commit_effect(
            resource, expected_term=2, expected_sequence=2,
            generation=3, checksum=CHECKSUM, revision_sha256=REVISION,
            nonce="a" * 32,
        )
    with pytest.raises(lab.RejectedCommit):
        lab.commit_effect(
            resource, expected_term=1, expected_sequence=2,
            generation=3, checksum=CHECKSUM, revision_sha256=REVISION,
            nonce=NONCE,
        )
    assert lab.resource_readback(resource) == before


def test_lost_ack_after_real_commit_is_uncertain_without_automatic_retry(stores):
    resource, anchor = stores
    with pytest.raises(lab.UncertainCommit, match="OUTCOME_UNCERTAIN"):
        applied(resource, lost=True)
    # COMMIT was real and durable inside this SQLite experiment, although
    # caller observed only uncertainty; no second logical effect is allowed.
    assert lab.resource_readback(resource)["commit_seq"] == 2
    assert lab.anchor_readback(anchor)["commit_seq"] == 1
    with pytest.raises(lab.UncertainCommit, match="NO|UNCERTAIN"):
        lab.automatic_retry_after_uncertain_commit(resource)
    with pytest.raises(lab.RejectedCommit):
        applied(resource)
    assert lab.resource_readback(resource)["commit_seq"] == 2


def test_resource_restore_with_highwater_retained_denies_stale_effect(stores, tmp_path):
    resource, anchor = stores
    old = tmp_path / "precommit-resource-snapshot.sqlite"
    shutil.copy2(resource, old)
    applied(resource)
    lab.operator_advance_anchor(
        anchor, expected_term=1, expected_seq=1,
        new_term=1, new_seq=2, new_nonce=NONCE,
    )
    shutil.copy2(old, resource)
    assert lab.resource_readback(resource)["commit_seq"] == 1
    assert lab.anchor_readback(anchor)["commit_seq"] == 2


def test_rollback_of_the_external_anchor_reopens_previously_accepted_position(stores, tmp_path):
    resource, anchor = stores
    old_anchor = tmp_path / "old-anchor-backup.sqlite"
    shutil.copy2(anchor, old_anchor)
    applied(resource)
    lab.operator_advance_anchor(
        anchor, expected_term=1, expected_seq=1,
        new_term=1, new_seq=2, new_nonce=NONCE,
    )
    assert lab.anchor_readback(anchor)["commit_seq"] == 2
    shutil.copy2(old_anchor, anchor)  # Attacker restores only anchor store.
    assert lab.anchor_readback(anchor)["commit_seq"] == 1
    assert lab.resource_readback(resource)["commit_seq"] == 2


def test_privileged_sql_writer_can_fabricate_resource_effect_and_receipt(stores):
    resource, _ = stores
    # CRITICAL P0: bypass the resource's authorized commit API entirely.
    # Since the same OS user controls its DB, no authority separation exists.
    with sqlite3.connect(resource) as db:
        db.execute("""UPDATE resource_state SET term=1, commit_seq=2,
            generation=2, checksum=?, revision_sha256=?, nonce=?""",
            (CHECKSUM, REVISION, NONCE))
        db.execute("INSERT INTO commit_receipts VALUES (?,?,?,?,?,?)",
                   (2, 1, NONCE, 2, CHECKSUM, REVISION))
    assert lab.resource_readback(resource)["committed"] is True


def test_invalid_receipt_does_not_pass_independent_readback(stores):
    resource, _ = stores
    with sqlite3.connect(resource) as db:
        db.execute("UPDATE resource_state SET commit_seq=2")
    assert lab.resource_readback(resource)["committed"] is False


def test_external_anchor_cas_and_monotonicity(stores):
    _, anchor = stores
    lab.operator_advance_anchor(
        anchor, expected_term=1, expected_seq=1, new_term=1, new_seq=2,
        new_nonce=NONCE,
    )
    with pytest.raises(lab.RejectedCommit):
        lab.operator_advance_anchor(
            anchor, expected_term=1, expected_seq=1, new_term=1,
            new_seq=2, new_nonce=NONCE,
        )
    with pytest.raises(lab.RejectedCommit):
        lab.operator_advance_anchor(
            anchor, expected_term=1, expected_seq=2, new_term=1,
            new_seq=1, new_nonce=NONCE,
        )
    assert lab.anchor_readback(anchor)["commit_seq"] == 2


def test_new_signature_harness_accepts_only_matching_committed_resource(stores):
    resource, anchor = stores
    applied(resource)
    assert evaluate_signed(resource, anchor) == (True, "RESEARCH_CONSISTENCY_ONLY")
    lab.operator_advance_anchor(
        anchor, expected_term=1, expected_seq=1,
        new_term=1, new_seq=2, new_nonce=NONCE,
    )
    assert evaluate_signed(resource, anchor) == (
        False, "witness_replay_or_restored_highwater"
    )


def test_real_resource_restore_mismatch_is_not_enough_to_hide_fictional_signature(stores, tmp_path):
    resource, anchor = stores
    saved = tmp_path / "resource_before.sqlite"
    shutil.copy2(resource, saved)
    applied(resource)
    shutil.copy2(saved, resource)
    # Signer can still claim seq2, but the SQLite readback reports seq1.
    assert lab.resource_readback(resource)["commit_seq"] == 1
    with pytest.raises(lab.RejectedCommit):
        lab.commit_effect(
            resource, expected_term=1, expected_sequence=2,
            generation=2, checksum=CHECKSUM, revision_sha256=REVISION,
            nonce=NONCE,
        )


def test_signed_fiction_with_privileged_sql_readback_still_passes_no_go(stores):
    resource, anchor = stores
    with sqlite3.connect(resource) as db:
        db.execute("UPDATE resource_state SET commit_seq=2, generation=2, checksum=?, revision_sha256=?, nonce=?",
                   (CHECKSUM, REVISION, NONCE))
        db.execute("INSERT INTO commit_receipts VALUES (?,?,?,?,?,?)",
                   (2, 1, NONCE, 2, CHECKSUM, REVISION))
    # VALID CRYPTO + fabricated SQL is still only simulated consistency.
    assert evaluate_signed(resource, anchor) == (True, "RESEARCH_CONSISTENCY_ONLY")


def test_effect_can_change_after_both_reads_before_caller_uses_decision(stores):
    resource, anchor = stores
    applied(resource)
    observations = []
    def readback_then_mutate():
        state = lab.resource_readback(resource)
        observations.append(state)
        if len(observations) == 2:
            with sqlite3.connect(resource) as db:
                db.execute("UPDATE resource_state SET generation=999")
        return state
    assert evaluate_signed(
        resource, anchor, callback=readback_then_mutate,
    ) == (True, "RESEARCH_CONSISTENCY_ONLY")
    assert lab.resource_readback(resource)["committed"] is False
    # CRITICAL P0: dual readback not a resource-owned finalization fence.


def test_anchor_rollback_reenables_signed_replay_no_go(stores, tmp_path):
    resource, anchor = stores
    applied(resource)
    assert evaluate_signed(resource, anchor) == (True, "RESEARCH_CONSISTENCY_ONLY")
    saved = tmp_path / "old-external-anchor.sqlite"
    shutil.copy2(anchor, saved)
    lab.operator_advance_anchor(
        anchor, expected_term=1, expected_seq=1, new_term=1, new_seq=2,
        new_nonce=NONCE,
    )
    assert evaluate_signed(resource, anchor)[0] is False
    shutil.copy2(saved, anchor)
    assert evaluate_signed(resource, anchor) == (True, "RESEARCH_CONSISTENCY_ONLY")


def test_same_uncertain_ack_can_mean_no_commit_or_committed_effect(tmp_path):
    outcomes = []
    for label, before in (("before", True), ("after", False)):
        case = tmp_path / label
        case.mkdir()
        resource, anchor = case / "resource.sqlite", case / "anchor.sqlite"
        lab.initialize(resource, anchor)
        try:
            lab.commit_effect(
                resource, expected_term=1, expected_sequence=1,
                generation=2, checksum=CHECKSUM, revision_sha256=REVISION,
                nonce=NONCE,
                lose_ack_before_commit=before,
                lose_ack_after_commit=not before,
            )
        except lab.UncertainCommit as error:
            outcomes.append((str(error), lab.resource_readback(resource)["commit_seq"]))
        else:
            pytest.fail("both simulated lost-ACK outcomes must be uncertain")
        assert lab.anchor_readback(anchor)["commit_seq"] == 1
    assert outcomes == [
        ("ACK_NOT_OBSERVED_COMMIT_OUTCOME_UNCERTAIN", 1),
        ("ACK_NOT_OBSERVED_COMMIT_OUTCOME_UNCERTAIN", 2),
    ]


def test_external_highwater_blocks_signed_replay_after_resource_restore(stores, tmp_path):
    resource, anchor = stores
    old = tmp_path / "resource-precommit.sqlite"
    shutil.copy2(resource, old)
    applied(resource)
    lab.operator_advance_anchor(
        anchor, expected_term=1, expected_seq=1,
        new_term=1, new_seq=2, new_nonce=NONCE,
    )
    shutil.copy2(old, resource)
    assert lab.resource_readback(resource)["commit_seq"] == 1
    assert evaluate_signed(resource, anchor) == (
        False, "witness_replay_or_restored_highwater"
    )
