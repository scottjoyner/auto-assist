"""Actual disposable SQLite transactions + synthetic private signer in test memory.

No network, SQL service, device, real key, production graph, or release calls.
The import-only verifier has NO private-key API or admission path.
"""
from __future__ import annotations

import base64
import importlib.util
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts/research-asymmetric-resource-receipt.py"
)
spec = importlib.util.spec_from_file_location("asymmetric_resource_receipt_research", SOURCE)
assert spec and spec.loader
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
NOW = 1792000000000
CHALLENGE = "1" * 64
NONCE = "e" * 32
IDENTITY = "auto-router-runtime-projection"
KEY_ID = "independent-resource-commit-research-v1"


def example():
    signer = Ed25519PrivateKey.generate()
    pub = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    roots = {KEY_ID: pub}
    effect = {
        "resource_id": IDENTITY, "term": 4, "commit_seq": 12,
        "generation": 27, "checksum": "a" * 64,
        "revision_sha256": "b" * 64, "committed": True,
    }
    anchor = {"term": 4, "commit_seq": 11, "last_nonce": "c" * 32}
    receipt = {
        "schema_version": 1, "audience": "synthetic-resource-commit-observation",
        **{k: effect[k] for k in (
            "resource_id", "term", "commit_seq", "generation",
            "checksum", "revision_sha256",
        )},
        "nonce": NONCE, "challenge": CHALLENGE, "key_epoch": 7,
        "issued_at_ms": NOW - 1000, "expires_at_ms": NOW + 15000,
    }

    def sign(claim, private=None):
        private = signer if private is None else private
        return {
            "key_id": KEY_ID,
            "receipt": deepcopy(claim),
            "signature": base64.b64encode(private.sign(v.canonical(claim))).decode(),
        }

    return sign, receipt, effect, anchor, roots


def check(
    envelope, effect, anchor, roots, *, callback=None,
    challenge=CHALLENGE, key_epoch=7, now=NOW,
):
    return v.verify(
        envelope, pinned_public_keys=roots,
        pinned_key_epoch=key_epoch, independently_expected_effect=effect,
        independently_held_anchor=anchor,
        independently_issued_challenge=challenge,
        readback=(lambda: deepcopy(effect)) if callback is None else callback,
        now_ms=now,
    )


def test_matching_asymmetric_receipt_is_readonly_research_observation():
    sign, receipt, effect, anchor, roots = example()
    assert check(sign(receipt), effect, anchor, roots) == (
        True, "RESEARCH_ASYMMETRIC_OBSERVATION_ONLY"
    )


@pytest.mark.parametrize("change,reason", [
    ({"key_epoch": 9}, "resource_receipt_wrong_key_epoch"),
    ({"schema_version": True}, "resource_receipt_invalid"),
    ({"audience": "production"}, "resource_receipt_invalid"),
    ({"resource_id": "unowned-effect"}, "resource_receipt_invalid"),
    ({"generation": 999}, "signed_receipt_effect_mismatch"),
    ({"checksum": "f"*64}, "signed_receipt_effect_mismatch"),
    ({"revision_sha256": "0"*64}, "signed_receipt_effect_mismatch"),
    ({"term": 5}, "signed_receipt_effect_mismatch"),
    ({"commit_seq": 13}, "signed_receipt_effect_mismatch"),
    ({"nonce": "nonhex"}, "resource_receipt_invalid"),
    ({"challenge": "0"*64}, "resource_challenge_replay"),
    ({"expires_at_ms": NOW + 65_000}, "resource_receipt_expired_or_future"),
    ({"issued_at_ms": NOW + 1}, "resource_receipt_expired_or_future"),
])
def test_attacker_can_resign_malformed_or_unexpected_claim_but_cannot_bind_it(
    change, reason,
):
    sign, receipt, effect, anchor, roots = example()
    receipt.update(change)
    assert check(sign(receipt), effect, anchor, roots) == (False, reason)


def test_database_only_forged_effect_not_sufficient_without_signing_private_key():
    sign, receipt, effect, anchor, roots = example()
    attacker = Ed25519PrivateKey.generate()
    forged = sign(receipt, private=attacker)
    assert check(forged, effect, anchor, roots) == (
        False, "resource_receipt_signature_invalid"
    )


def test_public_verification_root_cannot_sign():
    sign, receipt, effect, anchor, roots = example()
    assert set(roots) == {KEY_ID}
    assert isinstance(roots[KEY_ID], bytes) and len(roots[KEY_ID]) == 32
    assert "Ed25519PrivateKey" not in SOURCE.read_text()


def test_missing_pinned_public_key_and_wrong_key_epoch_fail_closed():
    sign, receipt, effect, anchor, roots = example()
    assert check(sign(receipt), effect, anchor, {}) == (
        False, "independent_pinned_public_key_missing"
    )
    assert check(sign(receipt), effect, anchor, None) == (
        False, "independent_public_key_custody_missing"
    )
    assert check(sign(receipt), effect, anchor, roots, key_epoch=0) == (
        False, "independent_public_key_custody_missing"
    )
    assert check(sign(receipt), effect, anchor, roots, key_epoch=8) == (
        False, "resource_receipt_wrong_key_epoch"
    )


def test_signed_receipt_expires_or_replay_new_challenge_denied():
    sign, receipt, effect, anchor, roots = example()
    signed = sign(receipt)
    assert check(signed, effect, anchor, roots, now=NOW + 20000) == (
        False, "resource_receipt_expired_or_future"
    )
    assert check(signed, effect, anchor, roots, challenge="f"*64) == (
        False, "resource_challenge_replay"
    )


def test_malformed_signature_denied_without_exposing_input():
    sign, receipt, effect, anchor, roots = example()
    envelope = sign(receipt)
    envelope["signature"] = "sensitive-nonbase64"
    assert check(envelope, effect, anchor, roots) == (
        False, "resource_receipt_signature_invalid"
    )


@pytest.mark.parametrize("position", [
    {"term": 4, "commit_seq": 12, "last_nonce": "c" * 32},
    {"term": 4, "commit_seq": 13, "last_nonce": "c" * 32},
    {"term": 3, "commit_seq": 11, "last_nonce": "c" * 32},
    {"term": 4, "commit_seq": 11, "last_nonce": NONCE},
])
def test_no_same_old_future_or_wrong_term_anchor(position):
    sign, receipt, effect, anchor, roots = example()
    assert check(sign(receipt), effect, position, roots) == (
        False, "signed_receipt_not_next_monotonic_position"
    )


def test_missing_resource_readback_denied():
    sign, receipt, effect, anchor, roots = example()
    assert v.verify(
        sign(receipt), pinned_public_keys=roots, pinned_key_epoch=7,
        independently_expected_effect=effect, independently_held_anchor=anchor,
        independently_issued_challenge=CHALLENGE, readback=None, now_ms=NOW,
    ) == (False, "authoritative_resource_readback_missing")


def test_resource_effect_changes_between_readbacks_denied():
    sign, receipt, effect, anchor, roots = example()
    seen = iter([deepcopy(effect), dict(effect, generation=98)])
    assert check(
        sign(receipt), effect, anchor, roots, callback=lambda: next(seen)
    ) == (False, "resource_changed_during_readback")


def test_reported_fiction_without_actual_committed_effect_denied():
    sign, receipt, effect, anchor, roots = example()
    actual = dict(effect, committed=False)
    assert check(
        sign(receipt), effect, anchor, roots, callback=lambda: actual
    ) == (False, "resource_readback_untrusted_shape")


def test_readback_exception_becomes_bounded_denial():
    sign, receipt, effect, anchor, roots = example()
    def failed():
        raise RuntimeError("SYNTHETIC UNTRUSTED RESPONSE")
    assert check(
        sign(receipt), effect, anchor, roots, callback=failed
    ) == (False, "resource_readback_unavailable")


def test_trust_root_replacement_remains_no_go():
    sign, receipt, effect, anchor, roots = example()
    compromised_signer = Ed25519PrivateKey.generate()
    roots[KEY_ID] = compromised_signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert check(
        sign(receipt, private=compromised_signer), effect, anchor, roots
    ) == (True, "RESEARCH_ASYMMETRIC_OBSERVATION_ONLY")


def test_colluding_attestor_and_fake_db_readback_remains_no_go():
    sign, receipt, effect, anchor, roots = example()
    effect["generation"] = 999  # Fiction provided by untrusted authority.
    receipt["generation"] = 999
    assert check(sign(receipt), effect, anchor, roots) == (
        True, "RESEARCH_ASYMMETRIC_OBSERVATION_ONLY"
    )


def test_restored_anchor_replays_same_signed_receipt_no_go():
    sign, receipt, effect, anchor, roots = example()
    old = deepcopy(anchor)
    saved = sign(receipt)
    assert check(saved, effect, old, roots)[0] is True
    anchor["commit_seq"] = 12
    assert check(saved, effect, anchor, roots) == (
        False, "signed_receipt_not_next_monotonic_position"
    )
    assert check(saved, effect, old, roots) == (
        True, "RESEARCH_ASYMMETRIC_OBSERVATION_ONLY"
    )


def test_two_independent_verifiers_pass_without_external_consumption_no_go():
    sign, receipt, effect, anchor, roots = example()
    envelope = sign(receipt)
    assert check(envelope, effect, anchor, roots) == check(
        envelope, effect, anchor, roots
    ) == (True, "RESEARCH_ASYMMETRIC_OBSERVATION_ONLY")


@pytest.fixture
def disposable_db(tmp_path):
    db_file = tmp_path / "resource.sqlite"
    db = sqlite3.connect(db_file)
    db.execute(
        """CREATE TABLE effect (
          resource_id TEXT PRIMARY KEY, term INTEGER NOT NULL,
          commit_seq INTEGER NOT NULL, generation INTEGER NOT NULL,
          checksum TEXT NOT NULL, revision_sha256 TEXT NOT NULL,
          committed INTEGER NOT NULL
        )"""
    )
    db.execute("""CREATE TABLE receipt (
      commit_seq INTEGER PRIMARY KEY, resource_id TEXT NOT NULL,
      checksum TEXT NOT NULL
    )""")
    db.commit()
    db.close()

    def persist(effect):
        with sqlite3.connect(db_file) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "INSERT OR REPLACE INTO effect VALUES (?,?,?,?,?,?,?)",
                (
                    effect["resource_id"], effect["term"], effect["commit_seq"],
                    effect["generation"], effect["checksum"],
                    effect["revision_sha256"], int(effect["committed"]),
                ),
            )
            writer.execute(
                "INSERT OR REPLACE INTO receipt VALUES (?,?,?)",
                (effect["commit_seq"], effect["resource_id"], effect["checksum"]),
            )

    def readback():
        with sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True) as observer:
            row = observer.execute("SELECT * FROM effect").fetchone()
            if row is None:
                return None
            rcpt = observer.execute(
                "SELECT checksum FROM receipt WHERE commit_seq=?", (row[2],)
            ).fetchone()
            return {
                "resource_id": row[0], "term": row[1],
                "commit_seq": row[2], "generation": row[3],
                "checksum": row[4], "revision_sha256": row[5],
                "committed": bool(row[6]) and rcpt == (row[4],),
            }

    return persist, readback, db_file


def test_real_disposable_sqlite_commit_with_external_test_signer(disposable_db):
    sign, receipt, effect, anchor, roots = example()
    persist, readback, db_file = disposable_db
    persist(effect)  # Actual SQLite COMMIT and matching receipt.
    assert db_file.is_file()
    assert check(sign(receipt), effect, anchor, roots, callback=readback) == (
        True, "RESEARCH_ASYMMETRIC_OBSERVATION_ONLY"
    )


def test_direct_privileged_sql_fiction_without_signer_still_denied(disposable_db):
    sign, receipt, effect, anchor, roots = example()
    persist, readback, db_file = disposable_db
    persist(effect)
    forged_key = Ed25519PrivateKey.generate()  # DBA lacks pinned signer key.
    with sqlite3.connect(db_file) as admin:
        admin.execute("UPDATE effect SET generation=888")
    forged_effect = dict(effect, generation=888)
    forged_receipt = dict(receipt, generation=888)
    assert check(
        sign(forged_receipt, private=forged_key), forged_effect, anchor,
        roots, callback=readback,
    ) == (False, "resource_receipt_signature_invalid")


def test_privileged_sql_can_change_effect_after_verification_no_go(disposable_db):
    sign, receipt, effect, anchor, roots = example()
    persist, readback, db_file = disposable_db
    persist(effect)
    assert check(sign(receipt), effect, anchor, roots, callback=readback)[0] is True
    with sqlite3.connect(db_file) as admin:
        admin.execute("UPDATE effect SET generation=777")
    assert readback()["generation"] == 777  # Prior True was not a lock.


def test_source_module_has_no_release_or_real_signer_entrypoint():
    source = SOURCE.read_text()
    assert "NO_RESOURCE_ADMISSION_OR_PRODUCTION_ENTRYPOINT" in source
    assert "Ed25519PublicKey" in source
    assert "Ed25519PrivateKey" not in source


def test_signed_future_effect_is_denied_without_postcommit_resource_readback(disposable_db):
    sign, receipt, effect, anchor, roots = example()
    _, readback, _ = disposable_db
    # Attestor signature is valid, but resource has not committed anything.
    assert check(sign(receipt), effect, anchor, roots, callback=readback) == (
        False, "resource_readback_untrusted_shape"
    )


def test_compromised_signer_plus_privileged_sql_can_fabricate_commit_no_go(disposable_db):
    sign, receipt, effect, anchor, roots = example()
    persist, readback, db_file = disposable_db
    # Synthetic common authority both signs and fabricates the apparent
    # effect/receipt rows. This passes a purely read-only external checker.
    persist(effect)
    with sqlite3.connect(db_file) as privileged:
        privileged.execute("UPDATE effect SET generation=1001")
    invented = dict(effect, generation=1001)
    receipt["generation"] = 1001
    assert check(sign(receipt), invented, anchor, roots, callback=readback) == (
        True, "RESEARCH_ASYMMETRIC_OBSERVATION_ONLY"
    )
    # The db administrator and signer colluding still defeats this proof.
