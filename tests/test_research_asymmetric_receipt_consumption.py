"""Disposable SQLite anchor CAS over externally signed, synthetic receipts."""
from __future__ import annotations

import base64
import concurrent.futures
import importlib.util
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/research-consume-asymmetric-receipt.py"
spec = importlib.util.spec_from_file_location("research_receipt_consumption", SCRIPT)
assert spec and spec.loader
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)
source_verifier = lab._verifier()
NOW = 1792000000000
CHALLENGE = "a" * 64
NONCE = "e" * 32
KEY_ID = "independent-resource-commit-research-v1"


@pytest.fixture
def setup(tmp_path):
    resource = tmp_path / "synthetic_resource.sqlite"
    anchor = tmp_path / "synthetic_external_anchor.sqlite"
    lab.initialize_disposable_anchor(anchor)
    signer = Ed25519PrivateKey.generate()
    roots = {KEY_ID: signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )}
    effect = {
        "resource_id": "auto-router-runtime-projection",
        "term": 4, "commit_seq": 12, "generation": 27,
        "checksum": "b" * 64, "revision_sha256": "d" * 64,
        "committed": True,
    }
    prior = {"term": 4, "commit_seq": 11, "last_nonce": "c" * 32}
    receipt = {
        "schema_version": 1, "audience": "synthetic-resource-commit-observation",
        **{k: effect[k] for k in (
            "resource_id", "term", "commit_seq", "generation",
            "checksum", "revision_sha256",
        )},
        "nonce": NONCE, "challenge": CHALLENGE,
        "key_epoch": 7, "issued_at_ms": NOW - 1000,
        "expires_at_ms": NOW + 5000,
    }

    def sign(claim, key=None):
        private = signer if key is None else key
        return {
            "key_id": KEY_ID, "receipt": deepcopy(claim),
            "signature": base64.b64encode(private.sign(source_verifier.canonical(claim))).decode(),
        }

    with sqlite3.connect(resource) as db:
        db.execute("CREATE TABLE effect (resource_id TEXT PRIMARY KEY, term INTEGER,"
                   "commit_seq INTEGER, generation INTEGER, checksum TEXT,"
                   "revision_sha256 TEXT, committed INTEGER)")
        db.execute("INSERT INTO effect VALUES (?,?,?,?,?,?,?)",
                   (effect["resource_id"], effect["term"], effect["commit_seq"],
                    effect["generation"], effect["checksum"],
                    effect["revision_sha256"], 1))

    def readback():
        with sqlite3.connect(f"{resource.as_uri()}?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute("SELECT * FROM effect").fetchone()
        if row is None:
            return None
        return dict(zip((
            "resource_id", "term", "commit_seq", "generation",
            "checksum", "revision_sha256", "committed"
        ), (*row[:6], row[6] == 1), strict=True))

    def invoke(
        signed=None, expected=None, old=None, challenge=CHALLENGE, callback=None,
        roots_arg=None, lose_ack=False, now=NOW,
    ):
        return lab.consume_once(
            anchor, sign(receipt) if signed is None else signed,
            pinned_public_keys=roots if roots_arg is None else roots_arg,
            expected_effect=effect if expected is None else expected,
            expected_challenge=challenge, expected_previous=prior if old is None else old,
            readback=readback if callback is None else callback,
            now_ms=now, lose_ack_after_commit=lose_ack,
        )

    return {
        "anchor": anchor, "resource": resource, "roots": roots,
        "effect": effect, "prior": prior, "receipt": receipt,
        "sign": sign, "readback": readback, "invoke": invoke,
    }


def test_valid_receipt_atomically_updates_anchor_and_challenge(setup):
    s = setup
    assert s["invoke"]() == (True, "RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY")
    assert lab.read_anchor(s["anchor"]) == {
        "term": 4, "commit_seq": 12, "last_nonce": NONCE, "pinned_key_epoch": 7,
    }
    with sqlite3.connect(s["anchor"]) as db:
        assert db.execute("SELECT COUNT(*) FROM spent_challenges").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM consumed_receipts").fetchone() == (1,)


def test_same_signature_replay_is_rejected_and_state_does_not_advance(setup):
    s = setup
    envelope = s["sign"](s["receipt"])
    assert s["invoke"](signed=envelope)[0] is True
    assert s["invoke"](signed=envelope) == (False, "stale_or_restored_anchor_cas")
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 12


def test_two_concurrent_consumers_only_one_succeeds(setup):
    s = setup
    envelope = s["sign"](s["receipt"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: s["invoke"](signed=envelope), range(2)))
    assert sum(accepted for accepted, _ in results) == 1
    assert sum(reason == "stale_or_restored_anchor_cas" for _, reason in results) == 1
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 12


def test_lost_ack_after_real_anchor_commit_is_not_permission_to_retry(setup):
    s = setup
    with pytest.raises(lab.UncertainReceiptCommit, match="OUTCOME_UNCERTAIN"):
        s["invoke"](lose_ack=True)
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 12
    assert s["invoke"]() == (False, "stale_or_restored_anchor_cas")


def test_invalid_signature_never_consumes_challenge(setup):
    s = setup
    attacker = Ed25519PrivateKey.generate()
    assert s["invoke"](signed=s["sign"](s["receipt"], attacker)) == (
        False, "resource_receipt_signature_invalid"
    )
    with sqlite3.connect(s["anchor"]) as db:
        assert db.execute("SELECT COUNT(*) FROM spent_challenges").fetchone() == (0,)
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 11


def test_no_resource_effect_denies_anchor_advance(setup):
    s = setup
    with sqlite3.connect(s["resource"]) as db:
        db.execute("DELETE FROM effect")
    assert s["invoke"]() == (False, "resource_readback_untrusted_shape")
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 11


@pytest.mark.parametrize("mutation,reason", [
    ({"generation": 123}, "signed_receipt_effect_mismatch"),
    ({"challenge": "0" * 64}, "resource_challenge_replay"),
    ({"term": 5}, "signed_receipt_effect_mismatch"),
    ({"key_epoch": 8}, "resource_receipt_wrong_key_epoch"),
    ({"commit_seq": 13}, "signed_receipt_effect_mismatch"),
])
def test_resigned_wrong_claim_does_not_consume(mutation, reason, setup):
    s = setup
    claim = deepcopy(s["receipt"])
    claim.update(mutation)
    assert s["invoke"](signed=s["sign"](claim)) == (False, reason)
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 11


def test_legacy_or_wrong_client_checkpoint_denied(setup):
    s = setup
    fake = dict(s["prior"], commit_seq=12)
    assert s["invoke"](old=fake) == (False, "stale_or_restored_anchor_cas")


def test_key_epoch_is_pinned_in_resource_anchor_transaction(setup):
    s = setup
    with sqlite3.connect(s["anchor"]) as admin:
        admin.execute("UPDATE highwater SET pinned_key_epoch=8")
    assert s["invoke"]() == (False, "resource_receipt_wrong_key_epoch")


def test_full_snapshot_restore_reopens_receipt_replay_critical_no_go(setup, tmp_path):
    s = setup
    old = tmp_path / "old_anchor_snapshot.sqlite"
    shutil.copy2(s["anchor"], old)
    assert s["invoke"]()[0] is True
    shutil.copy2(old, s["anchor"])
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 11
    assert s["invoke"]() == (True, "RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY")


def test_privileged_sql_override_of_anchor_reopens_receipt_critical_no_go(setup):
    s = setup
    assert s["invoke"]()[0] is True
    with sqlite3.connect(s["anchor"]) as administrator:
        administrator.execute("DELETE FROM consumed_receipts")
        administrator.execute("DELETE FROM spent_challenges")
        administrator.execute(
            "UPDATE highwater SET commit_seq=11,last_nonce=? WHERE resource_id=?",
            ("c" * 32, "auto-router-runtime-projection"),
        )
    assert s["invoke"]()[0] is True


def test_colluding_signer_with_fabricated_resource_readback_remains_no_go(setup):
    s = setup
    lie = dict(s["effect"], generation=777)
    altered = dict(s["receipt"], generation=777)
    assert s["invoke"](
        signed=s["sign"](altered), expected=lie, callback=lambda: lie,
    ) == (True, "RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY")
    # No real committed effect with generation=777 existed in resource DB.
    assert s["readback"]()["generation"] == 27


def test_privileged_resource_effect_mutation_after_cas_is_not_fenced(setup):
    s = setup
    assert s["invoke"]()[0] is True
    with sqlite3.connect(s["resource"]) as db:
        db.execute("UPDATE effect SET generation=999")
    assert s["readback"]()["generation"] == 999
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 12


def test_invalid_external_pin_is_not_source_signing_authority(setup):
    s = setup
    assert s["invoke"](roots_arg={}) == (
        False, "independent_pinned_public_key_missing"
    )


def test_bootstrap_refuses_overwriting_existing_state(setup):
    s = setup
    with pytest.raises(ValueError):
        lab.initialize_disposable_anchor(s["anchor"])
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 11


def test_source_disables_real_execution_and_does_not_use_private_key():
    source = SCRIPT.read_text()
    assert "RESEARCH_ONLY_NO_REAL_ANCHOR_OR_RELEASE_ENTRYPOINT" in source
    assert "Ed25519PrivateKey" not in source
    assert "BEGIN IMMEDIATE" in source
    assert "spent_challenges" in source and "consumed_receipts" in source


def test_resource_changes_during_second_read_before_anchor_commit_no_go(setup):
    """Even a locally atomic anchor update cannot fence the other resource."""
    s = setup
    calls = 0

    def adversarial_readback():
        nonlocal calls
        snapshot = s["readback"]()
        calls += 1
        if calls == 2:
            # The resource commits a different effect after the SECOND
            # read's snapshot, but before anchor consumption commits.
            with sqlite3.connect(s["resource"]) as db:
                db.execute("UPDATE effect SET generation=989")
        return snapshot

    assert s["invoke"](callback=adversarial_readback) == (
        True, "RESEARCH_DISPOSABLE_ANCHOR_CAS_ONLY"
    )
    assert calls == 2
    assert s["readback"]()["generation"] == 989
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 12


def test_unavailable_resource_readback_does_not_advance_or_consume(setup):
    s = setup
    def broken():
        raise RuntimeError("no synthetic resource witness")
    assert s["invoke"](callback=broken) == (
        False, "resource_readback_unavailable"
    )
    assert lab.read_anchor(s["anchor"])["commit_seq"] == 11
    with sqlite3.connect(s["anchor"]) as conn:
        assert conn.execute("SELECT count(*) FROM spent_challenges").fetchone() == (0,)


def test_missing_anchor_fails_closed_without_creating_database(setup):
    s = setup
    s["anchor"].unlink()
    assert s["invoke"]() == (False, "anchor_unavailable_no_authority")
    assert not s["anchor"].exists()


@pytest.mark.parametrize("actually_commits", [False, True])
def test_commit_api_failure_is_uncertain_whether_transaction_applied(
    setup, monkeypatch, actually_commits,
):
    s = setup
    normal_connect = sqlite3.connect

    class UncertainConnection:
        def __init__(self, native):
            self.native = native

        def __getattr__(self, name):
            return getattr(self.native, name)

        def commit(self):
            if actually_commits:
                self.native.commit()
            raise sqlite3.OperationalError("synthetic_lost_commit_ack")

    def inject(*args, **kwargs):
        native = normal_connect(*args, **kwargs)
        if kwargs.get("uri") and str(args[0]).endswith("?mode=rw"):
            return UncertainConnection(native)
        return native

    monkeypatch.setattr(lab.sqlite3, "connect", inject)
    with pytest.raises(lab.UncertainReceiptCommit, match="NO_RETRY"):
        s["invoke"]()
    # The caller sees the SAME uncertainty although physical outcomes differ.
    assert lab.read_anchor(s["anchor"])["commit_seq"] == (
        12 if actually_commits else 11
    )
