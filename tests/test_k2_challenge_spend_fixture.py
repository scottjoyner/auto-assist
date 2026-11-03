"""Offline research tests: synthetic, test-owned SQLite challenge spend only."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import test_x1_k2_signed_witness_offline as signed
from k2_challenge_spend_fixture import ChallengeHold, SyntheticChallengeLedger


@pytest.fixture
def context(tmp_path, monkeypatch):
    # Only test-generated Ed25519 keys and simulated process identities.
    doc, pin = signed.fixture()
    obs = doc["observation"]
    verifier = signed.module
    monkeypatch.setattr(verifier, "TRUSTED_WITNESS_KEY_SHA256", pin)
    monkeypatch.setattr(verifier, "APPROVED_RUNTIME_EXECUTABLE_SHA256", "e" * 64)
    monkeypatch.setattr(verifier, "APPROVED_PROXY_EXECUTABLE_SHA256", "c" * 64)
    monkeypatch.setattr(verifier, "TRUSTED_CURRENT_BOOT_ID_SHA256", "b" * 64)
    monkeypatch.setattr(verifier, "TRUSTED_CURRENT_PROCESS_IDENTITY", {
        "node": "x1-370",
        "boot_id_sha256": obs["process"]["boot_id_sha256"],
        "observed_at_ms": signed.NOW - 500,
        "runtime": {
            "pid": obs["process"]["pid"],
            "start_ticks": obs["process"]["start_ticks"],
        },
        "proxies": {
            name: {"pid": item["pid"], "start_ticks": item["start_ticks"]}
            for name, item in obs["process"]["proxies"].items()
        },
    })
    nonce = signed.CHALLENGE
    ledger = SyntheticChallengeLedger.bootstrap_new(tmp_path / "fake-out-of-band-cas.sqlite")
    raw = verifier.canonical(doc)
    return ledger, raw, nonce, verifier.verify


def issue(ledger, nonce, *, issued_ms=None, expires_ms=None):
    ledger.issue_for_test(
        nonce,
        issued_ms=signed.NOW - 12_000 if issued_ms is None else issued_ms,
        expires_ms=signed.NOW + 90_000 if expires_ms is None else expires_ms,
    )


def spend(context, *, raw=None, nonce=None, now_ms=signed.NOW):
    ledger, signed_bytes, challenge, verifier = context
    return ledger.consume_after_verification(
        signed_bytes if raw is None else raw,
        expected_nonce=challenge if nonce is None else nonce,
        now_ms=now_ms,
        verify_signed=verifier,
    )


def test_valid_signature_spends_once_but_can_never_admit(context):
    ledger, _, nonce, _ = context
    issue(ledger, nonce)
    result = spend(context)
    assert result["challenge_consumed_in_fixture"] is True
    assert result["sequence"] == 1
    assert result["decision"] == "HOLD"
    assert result["can_dispatch"] is False
    assert result["independent_custody_verified"] is False
    assert ledger.audit_counts() == {"issued": 1, "spent": 1, "highwater": 1}
    with pytest.raises(ChallengeHold, match="synthetic_challenge_replayed"):
        spend(context)
    assert ledger.audit_counts()["highwater"] == 1


def test_unissued_valid_receipt_cannot_consume(context):
    ledger = context[0]
    with pytest.raises(ChallengeHold, match="synthetic_challenge_not_preissued"):
        spend(context)
    assert ledger.audit_counts() == {"issued": 0, "spent": 0, "highwater": 0}


def test_forged_signature_does_not_burn_challenge(context):
    ledger, raw, nonce, verifier = context
    issue(ledger, nonce)
    doc = json.loads(raw)
    doc["observation"]["paths"]["lan"]["loaded_count"] += 1
    tampered = signed.module.canonical(doc)
    with pytest.raises(signed.module.WitnessHold, match="invalid_witness_signature"):
        spend(context, raw=tampered)
    assert ledger.audit_counts()["spent"] == 0
    assert spend(context)["sequence"] == 1


def test_replayed_receipt_rejected_after_database_reopen(context):
    ledger, raw, nonce, verifier = context
    issue(ledger, nonce)
    assert spend(context)["sequence"] == 1
    reopened = SyntheticChallengeLedger(ledger.path)
    with pytest.raises(ChallengeHold, match="synthetic_challenge_replayed"):
        reopened.consume_after_verification(
            raw, expected_nonce=nonce, now_ms=signed.NOW,
            verify_signed=verifier,
        )
    assert reopened.audit_counts()["highwater"] == 1


def test_reattempt_after_simulated_crash_ack_lost_is_not_accepted(context):
    ledger, raw, nonce, verifier = context
    issue(ledger, nonce)
    assert spend(context)["challenge_consumed_in_fixture"]
    # The consumer never received an acknowledgement, and tries the same
    # receipt from another process. Fail closed: no implicit retry lease.
    probe = (
        "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); "
        "print(db.execute('SELECT count(*) FROM challenges WHERE spent_ms IS NOT NULL').fetchone()[0]); "
        "print(db.execute('SELECT sequence FROM highwater WHERE id=1').fetchone()[0])"
    )
    output = subprocess.check_output(
        [sys.executable, "-c", probe, str(ledger.path)], text=True
    ).splitlines()
    assert output == ["1", "1"]
    with pytest.raises(ChallengeHold, match="synthetic_challenge_replayed"):
        SyntheticChallengeLedger(ledger.path).consume_after_verification(
            raw, expected_nonce=nonce, now_ms=signed.NOW,
            verify_signed=verifier,
        )


def test_two_competing_consumers_have_exactly_one_winner(context):
    ledger, _, nonce, _ = context
    issue(ledger, nonce)

    def attempt(_):
        try:
            spend(context)
            return "spent"
        except ChallengeHold as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("spent") == 1
    assert results.count("synthetic_challenge_replayed") == 7
    assert ledger.audit_counts() == {"issued": 1, "spent": 1, "highwater": 1}


def test_duplicate_issue_refused_without_replacing_original(context):
    ledger, _, nonce, _ = context
    issue(ledger, nonce)
    with pytest.raises(ChallengeHold, match="synthetic_challenge_already_issued"):
        issue(ledger, nonce, expires_ms=signed.NOW + 200_000)
    assert spend(context)["sequence"] == 1


@pytest.mark.parametrize("time", [
    signed.NOW - 2_000,
    signed.NOW + 50_000,
])
def test_challenge_window_mismatch_is_hold(context, time):
    ledger, _, nonce, _ = context
    if time < signed.NOW:
        # Issued after signing, even though challenge not yet expired.
        issue(ledger, nonce, issued_ms=time)
    else:
        # Expires before the signed receipt's declared expiry.
        issue(ledger, nonce, expires_ms=time)
    with pytest.raises(ChallengeHold, match="synthetic_challenge_window_mismatch"):
        spend(context)
    assert ledger.audit_counts()["spent"] == 0


def test_expired_issued_nonce_is_not_reused(context):
    ledger, _, nonce, _ = context
    issue(ledger, nonce, expires_ms=signed.NOW)
    with pytest.raises(ChallengeHold, match="synthetic_challenge_window_mismatch"):
        spend(context)
    assert ledger.audit_counts()["highwater"] == 0


def test_nonce_mismatch_does_not_burn_any_issue(context):
    ledger, _, nonce, _ = context
    issue(ledger, nonce)
    with pytest.raises(signed.module.WitnessHold, match="challenge_mismatch_or_replay"):
        spend(context, nonce="d" * 64)
    assert ledger.audit_counts()["spent"] == 0


def test_no_file_overwrite_and_symlink_rejection(tmp_path, context):
    ledger = context[0]
    with pytest.raises(ChallengeHold, match="synthetic_ledger_not_new"):
        SyntheticChallengeLedger.bootstrap_new(ledger.path)
    symlink = tmp_path / "linked-to-original.sqlite"
    symlink.symlink_to(ledger.path)
    with pytest.raises(ChallengeHold, match="synthetic_ledger_missing_or_symlink"):
        SyntheticChallengeLedger(symlink).audit_counts()
    with pytest.raises(ChallengeHold, match="synthetic_ledger_not_new"):
        SyntheticChallengeLedger.bootstrap_new(symlink)


def test_invalid_issue_does_not_create_nonce(context):
    ledger, _, nonce, _ = context
    with pytest.raises(ChallengeHold, match="synthetic_challenge_window_invalid"):
        issue(ledger, nonce, expires_ms=signed.NOW - 14_000)
    assert ledger.audit_counts()["issued"] == 0


def test_corrupt_highwater_rolls_back_spend(context):
    ledger, _, nonce, _ = context
    issue(ledger, nonce)
    with sqlite3.connect(ledger.path) as db:
        db.execute("DELETE FROM highwater")
    with pytest.raises(ChallengeHold, match="synthetic_highwater_missing"):
        spend(context)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT count(*) FROM challenges WHERE spent_ms IS NOT NULL"
        ).fetchone()[0] == 0
    with pytest.raises(ChallengeHold, match="synthetic_highwater_missing"):
        ledger.audit_counts()


def test_two_separate_challenges_advance_highwater(context):
    ledger, raw, nonce, verifier = context
    issue(ledger, nonce)
    assert spend(context)["sequence"] == 1
    doc = json.loads(raw)
    other = "f" * 64
    doc["expected_challenge_hex"] = other
    # Re-sign with an isolated test key, not the production verifier's key.
    key = signed.Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        encoding=signed.serialization.Encoding.Raw,
        format=signed.serialization.PublicFormat.Raw,
    )
    doc["witness_public_key_b64"] = signed.base64.b64encode(public).decode()
    doc["signature_b64"] = signed.base64.b64encode(
        key.sign(verifier.__globals__["canonical"]({
            k: v for k, v in doc.items() if k != "signature_b64"
        }))
    ).decode()
    old_pin = verifier.__globals__["TRUSTED_WITNESS_KEY_SHA256"]
    verifier.__globals__["TRUSTED_WITNESS_KEY_SHA256"] = hashlib.sha256(public).hexdigest()
    try:
        issue(ledger, other)
        result = ledger.consume_after_verification(
            verifier.__globals__["canonical"](doc),
            expected_nonce=other, now_ms=signed.NOW, verify_signed=verifier,
        )
        assert result["sequence"] == 2
        assert ledger.audit_counts() == {"issued": 2, "spent": 2, "highwater": 2}
    finally:
        verifier.__globals__["TRUSTED_WITNESS_KEY_SHA256"] = old_pin


def test_ledger_empty_production_approval_assertion(context):
    ledger, _, nonce, _ = context
    issue(ledger, nonce)
    outcome = spend(context)
    assert outcome["can_dispatch"] is False
    assert outcome["operator_approval_verified"] is False
    assert outcome["independent_custody_verified"] is False



def test_unsigned_or_boolean_only_caller_cannot_claim_spend(context):
    ledger, _, nonce, _ = context
    issue(ledger, nonce)

    def unsupported_verifier(*_args, **_kwargs):
        return {"signature_valid": True, "decision": "HOLD", "can_dispatch": False}

    with pytest.raises(ChallengeHold, match="synthetic_non_admitting_verification_required"):
        ledger.consume_after_verification(
            context[1], expected_nonce=nonce, now_ms=signed.NOW,
            verify_signed=unsupported_verifier,
        )
    assert ledger.audit_counts()["spent"] == 0


def test_plain_signature_result_explicitly_not_one_time(context):
    _, raw, nonce, verify = context
    result = verify(raw, expected_challenge_hex=nonce, now_ms=signed.NOW)
    assert result["signature_valid"] is True
    assert result["challenge_bound"] is True
    assert result["nonce_one_time_consumption_verified"] is False
    assert result["can_dispatch"] is False
