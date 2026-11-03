"""Synthetic operator -> issuer -> local enrollment, never production authority."""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from k2_authenticated_issuer_fixture import (  # noqa: E402
    NODE,
    PURPOSE,
    REQUEST_DOMAIN,
    SyntheticAuthenticatedIssuer,
    SyntheticIssuerHold,
    canonical,
    enroll_signed_issuance_for_test,
    make_operator_request,
    public_bytes,
    verify_issuer_receipt,
)
from k2_challenge_spend_fixture import ChallengeHold, SyntheticChallengeLedger  # noqa: E402

NOW = 1_800_000_000_000
NONCE = "a1" * 32


@pytest.fixture
def scenario(tmp_path):
    operator = Ed25519PrivateKey.generate()
    op_pin = hashlib.sha256(public_bytes(operator)).hexdigest()
    issuer = SyntheticAuthenticatedIssuer.bootstrap_new(
        tmp_path / "synthetic-operator-issuer.sqlite", operator_key_pin=op_pin,
    )
    ledger = SyntheticChallengeLedger.bootstrap_new(
        tmp_path / "synthetic-local-nonce-ledger.sqlite",
    )
    req = make_operator_request(
        operator, nonce=NONCE, issued_ms=NOW - 2000,
        expires_ms=NOW + 60_000, expected_sequence=1,
    )
    return issuer, ledger, operator, req, op_pin


def receipt_check(scenario, receipt, *, request=None, issuer_pin=None,
                  operator_pin=None, now_ms=NOW):
    issuer, _, _, original, op_pin = scenario
    return verify_issuer_receipt(
        receipt, request_bytes=original if request is None else request,
        issuer_key_pin=issuer.test_issuer_key_pin if issuer_pin is None else issuer_pin,
        operator_key_pin=op_pin if operator_pin is None else operator_pin,
        now_ms=now_ms,
    )


def enroll(scenario, receipt, *, request=None, issuer_pin=None, operator_pin=None):
    issuer, ledger, _, original, op_pin = scenario
    return enroll_signed_issuance_for_test(
        ledger, request_bytes=original if request is None else request,
        receipt_bytes=receipt,
        issuer_key_pin=issuer.test_issuer_key_pin if issuer_pin is None else issuer_pin,
        operator_key_pin=op_pin if operator_pin is None else operator_pin,
        now_ms=NOW,
    )


def resign(operator, data):
    body = {k: v for k, v in data.items() if k != "operator_signature_b64"}
    body["operator_signature_b64"] = base64.b64encode(
        operator.sign(REQUEST_DOMAIN + canonical(body))
    ).decode()
    return canonical(body)


def test_authorized_cas_issues_signed_receipt_and_local_enrollment_still_holds(scenario):
    issuer, ledger, _, req, _ = scenario
    receipt = issuer.issue(req, now_ms=NOW)
    verified = receipt_check(scenario, receipt)
    assert verified["receipt_authenticated_in_fixture"]
    assert verified["challenge_hex"] == NONCE
    assert verified["sequence"] == 1
    assert verified["independent_custody_verified"] is False
    assert verified["can_dispatch"] is False
    enrolled = enroll(scenario, receipt)
    assert enrolled["local_enrollment_complete"]
    assert enrolled["can_dispatch"] is False
    assert enrolled["decision"] == "HOLD"
    assert ledger.audit_counts() == {"issued": 1, "spent": 0, "highwater": 0}
    assert issuer.audit() == {"issued": 1, "highwater": 1}


def test_missing_operator_trust_root_prevents_issuer_creation(tmp_path):
    with pytest.raises(SyntheticIssuerHold, match="independent_operator_key_pin_missing"):
        SyntheticAuthenticatedIssuer.bootstrap_new(
            tmp_path / "forbidden.sqlite", operator_key_pin=""
        )
    assert not (tmp_path / "forbidden.sqlite").exists()


def test_wrong_operator_key_is_rejected_without_issuing(scenario):
    issuer, _, _, req, _ = scenario
    untrusted = Ed25519PrivateKey.generate()
    malicious = make_operator_request(
        untrusted, nonce=NONCE, issued_ms=NOW - 2000,
        expires_ms=NOW + 60_000, expected_sequence=1,
    )
    with pytest.raises(SyntheticIssuerHold, match="operator_signer_unapproved"):
        issuer.issue(malicious, now_ms=NOW)
    assert issuer.audit() == {"issued": 0, "highwater": 0}


def test_tampered_authorization_does_not_issue(scenario):
    issuer, _, _, req, _ = scenario
    obj = json.loads(req)
    obj["challenge_hex"] = "f" * 64
    with pytest.raises(SyntheticIssuerHold, match="operator_signature_invalid"):
        issuer.issue(canonical(obj), now_ms=NOW)
    assert issuer.audit()["highwater"] == 0


@pytest.mark.parametrize("variant, code", [
    ("purpose", "operator_request_wrong_domain_or_node"),
    ("node", "operator_request_wrong_domain_or_node"),
    ("challenge", "operator_request_identity_invalid"),
    ("boolean_seq", "operator_request_time_sequence_invalid"),
    ("expired", "operator_request_window_invalid"),
    ("overlong", "operator_request_window_invalid"),
    ("future", "operator_request_window_invalid"),
])
def test_bad_signed_operator_request_rejected_before_write(scenario, variant, code):
    issuer, _, operator, req, _ = scenario
    d = json.loads(req)
    if variant == "purpose":
        d["purpose"] = "cross-protocol-inference"
    elif variant == "node":
        d["node"] = "someone-else"
    elif variant == "challenge":
        d["challenge_hex"] = "not-a-32-byte-nonce"
    elif variant == "boolean_seq":
        d["expected_sequence"] = True
    elif variant == "expired":
        d["expires_at_ms"] = NOW - 1
    elif variant == "overlong":
        d["expires_at_ms"] = NOW + 600_000
    elif variant == "future":
        d["issued_at_ms"] = NOW + 1000
    with pytest.raises(SyntheticIssuerHold, match=code):
        issuer.issue(resign(operator, d), now_ms=NOW)
    assert issuer.audit()["highwater"] == 0


def test_noncanonical_and_duplicate_json_refused(scenario):
    issuer, _, _, req, _ = scenario
    with pytest.raises(SyntheticIssuerHold, match="noncanonical_json_bytes"):
        issuer.issue(json.dumps(json.loads(req), indent=2).encode(), now_ms=NOW)
    bogus = req.replace(b'"node":"x1-370"', b'"node":"x1-370","node":"x1-370"')
    with pytest.raises(SyntheticIssuerHold, match="ambiguous_duplicate_json_key"):
        issuer.issue(bogus, now_ms=NOW)
    assert issuer.audit()["highwater"] == 0


def test_domain_separated_signature_cannot_be_reused_from_other_protocol(scenario):
    issuer, _, operator, req, _ = scenario
    d = json.loads(req)
    unsigned = {k: v for k, v in d.items() if k != "operator_signature_b64"}
    d["operator_signature_b64"] = base64.b64encode(
        operator.sign(b"some-other-protocol\0" + canonical(unsigned))
    ).decode()
    with pytest.raises(SyntheticIssuerHold, match="operator_signature_invalid"):
        issuer.issue(canonical(d), now_ms=NOW)


def test_duplicate_nonce_same_request_refused_across_new_connection(scenario):
    issuer, _, _, req, _ = scenario
    issuer.issue(req, now_ms=NOW)
    duplicate = SyntheticAuthenticatedIssuer(
        issuer.path, operator_key_pin=issuer.operator_key_pin,
        test_issuer_key=issuer.test_issuer_key,
    )
    with pytest.raises(SyntheticIssuerHold, match="issuer_stale_or_competing_sequence"):
        duplicate.issue(req, now_ms=NOW)
    assert duplicate.audit()["highwater"] == 1


def test_duplicate_nonce_new_sequence_refused(scenario):
    issuer, _, operator, req, _ = scenario
    issuer.issue(req, now_ms=NOW)
    second = make_operator_request(
        operator, nonce=NONCE, issued_ms=NOW - 1000,
        expires_ms=NOW + 60_000, expected_sequence=2,
    )
    with pytest.raises(SyntheticIssuerHold, match="issuer_nonce_replayed"):
        issuer.issue(second, now_ms=NOW)
    assert issuer.audit() == {"issued": 1, "highwater": 1}


def test_new_nonce_second_sequence_advances_cas(scenario):
    issuer, _, operator, req, _ = scenario
    issuer.issue(req, now_ms=NOW)
    other = "b2" * 32
    second = make_operator_request(
        operator, nonce=other, issued_ms=NOW - 1000,
        expires_ms=NOW + 60_000, expected_sequence=2,
    )
    ack = issuer.issue(second, now_ms=NOW)
    assert verify_issuer_receipt(
        ack, request_bytes=second,
        issuer_key_pin=issuer.test_issuer_key_pin,
        operator_key_pin=issuer.operator_key_pin,
        now_ms=NOW,
    )["sequence"] == 2
    assert issuer.audit() == {"issued": 2, "highwater": 2}


def test_two_issuers_racing_same_request_one_success(scenario):
    issuer, _, _, req, _ = scenario

    def attempt(_):
        try:
            issuer.issue(req, now_ms=NOW)
            return "issued"
        except SyntheticIssuerHold as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(attempt, range(8)))
    assert got.count("issued") == 1
    assert got.count("issuer_stale_or_competing_sequence") == 7
    assert issuer.audit() == {"issued": 1, "highwater": 1}


def test_issuer_crash_after_commit_ack_loss_denies_retry(scenario):
    issuer, ledger, _, req, _ = scenario
    _lost_receipt = issuer.issue(req, now_ms=NOW)
    # Operator did not receive the reply; do not silently reissue.
    with pytest.raises(SyntheticIssuerHold, match="issuer_stale_or_competing_sequence"):
        issuer.issue(req, now_ms=NOW)
    assert ledger.audit_counts()["issued"] == 0
    assert issuer.audit()["highwater"] == 1


def test_wrong_issuer_key_pin_refuses_local_enrollment(scenario):
    issuer, ledger, _, req, _ = scenario
    ack = issuer.issue(req, now_ms=NOW)
    with pytest.raises(SyntheticIssuerHold, match="issuer_signer_unapproved"):
        enroll(scenario, ack, issuer_pin="f" * 64)
    assert ledger.audit_counts()["issued"] == 0


def test_issuer_ack_signature_tampering_refuses_local_enrollment(scenario):
    issuer, ledger, _, req, _ = scenario
    ack = issuer.issue(req, now_ms=NOW)
    d = json.loads(ack)
    encoded = d["issuer_signature_b64"]
    d["issuer_signature_b64"] = ("A" if encoded[0] != "A" else "B") + encoded[1:]
    with pytest.raises(SyntheticIssuerHold, match="issuer_receipt_signature_invalid"):
        enroll(scenario, canonical(d))
    assert ledger.audit_counts()["issued"] == 0


def test_issuer_ack_wrong_nonce_or_sequence_refuses_local_enrollment(scenario):
    issuer, ledger, _, req, _ = scenario
    ack = issuer.issue(req, now_ms=NOW)
    for field, value, reason in (
        ("challenge_hex", "f" * 64, "issuer_receipt_not_bound_to_request"),
        ("sequence", 2, "issuer_receipt_cas_or_request_mismatch"),
    ):
        d = json.loads(ack)
        d[field] = value
        with pytest.raises(SyntheticIssuerHold, match=reason):
            enroll(scenario, canonical(d))
    assert ledger.audit_counts()["issued"] == 0


def test_lost_local_issuance_ack_reenrollment_refuses_duplicate(scenario):
    issuer, ledger, _, req, _ = scenario
    ack = issuer.issue(req, now_ms=NOW)
    assert enroll(scenario, ack)["can_dispatch"] is False
    with pytest.raises(ChallengeHold, match="synthetic_challenge_already_issued"):
        enroll(scenario, ack)
    assert ledger.audit_counts()["issued"] == 1


def test_receipt_signed_by_test_issuer_does_not_approve_model_dispatch(scenario):
    issuer, _, _, req, _ = scenario
    ack = issuer.issue(req, now_ms=NOW)
    d = json.loads(ack)
    assert d["can_dispatch"] is False
    assert d["independent_custody_verified"] is False
    assert d["operator_approval_verified"] is False
    assert receipt_check(scenario, ack)["can_dispatch"] is False


def test_missing_issuer_database_and_symlink_refused(scenario, tmp_path):
    issuer, _, _, req, _ = scenario
    alias = tmp_path / "issuer-alias.sqlite"
    alias.symlink_to(issuer.path)
    with pytest.raises(SyntheticIssuerHold, match="issuer_missing_or_symlink"):
        SyntheticAuthenticatedIssuer(
            alias, operator_key_pin=issuer.operator_key_pin,
            test_issuer_key=issuer.test_issuer_key,
        ).audit()
    with pytest.raises(SyntheticIssuerHold, match="issuer_file_exists_or_invalid"):
        SyntheticAuthenticatedIssuer.bootstrap_new(
            alias, operator_key_pin=issuer.operator_key_pin,
        )


def test_missing_highwater_denies_without_new_issue(scenario):
    issuer, _, _, req, _ = scenario
    with sqlite3.connect(issuer.path) as db:
        db.execute("DELETE FROM highwater")
    with pytest.raises(SyntheticIssuerHold, match="issuer_highwater_missing"):
        issuer.issue(req, now_ms=NOW)
    with sqlite3.connect(issuer.path) as db:
        assert db.execute("SELECT COUNT(*) FROM issued").fetchone()[0] == 0


def test_bad_receipt_request_binding_denies_even_with_same_issuer(scenario):
    issuer, _, operator, req, _ = scenario
    ack = issuer.issue(req, now_ms=NOW)
    other = make_operator_request(
        operator, nonce="f" * 64, issued_ms=NOW - 2000,
        expires_ms=NOW + 60_000, expected_sequence=1,
    )
    with pytest.raises(SyntheticIssuerHold, match="issuer_receipt_not_bound_to_request"):
        receipt_check(scenario, ack, request=other)


def test_operator_request_stale_after_restart_does_not_reissue(scenario):
    issuer, _, _, req, _ = scenario
    with pytest.raises(SyntheticIssuerHold, match="operator_request_window_invalid"):
        issuer.issue(req, now_ms=NOW + 150_000)
    assert issuer.audit()["highwater"] == 0


def _sqlite_snapshot(source: Path, dest: Path) -> None:
    with sqlite3.connect(source) as old:
        with sqlite3.connect(dest) as backup:
            old.backup(backup)


def _sqlite_restore(source: Path, dest: Path) -> None:
    with sqlite3.connect(source) as backup:
        with sqlite3.connect(dest) as live:
            backup.backup(live)


def test_restoring_issuer_database_reissues_same_authenticated_nonce(
    scenario, tmp_path
):
    """Proves signer + file under ONE admin is not rollback resistance."""
    issuer, ledger, _, req, _ = scenario
    original = tmp_path / "issuer-before-sequence-one.sqlite"
    _sqlite_snapshot(issuer.path, original)
    first_receipt = issuer.issue(req, now_ms=NOW)
    assert receipt_check(scenario, first_receipt)["sequence"] == 1
    _sqlite_restore(original, issuer.path)
    replayed_receipt = issuer.issue(req, now_ms=NOW)
    assert replayed_receipt == first_receipt
    assert issuer.audit() == {"issued": 1, "highwater": 1}
    assert ledger.audit_counts()["issued"] == 0
    # This is NOT real external custody: same admin restored issuer DB.


def test_issuer_plus_local_ledger_common_custody_allows_repeat_enrollment(
    scenario, tmp_path
):
    """Two local files cannot prevent coordinated rollback/replay."""
    issuer, ledger, _, req, _ = scenario
    issuer_backup = tmp_path / "issuer-before.sqlite"
    local_backup = tmp_path / "local-before.sqlite"
    _sqlite_snapshot(issuer.path, issuer_backup)
    _sqlite_snapshot(ledger.path, local_backup)
    ack = issuer.issue(req, now_ms=NOW)
    assert enroll(scenario, ack)["local_enrollment_complete"]
    _sqlite_restore(issuer_backup, issuer.path)
    _sqlite_restore(local_backup, ledger.path)
    ack_again = issuer.issue(req, now_ms=NOW)
    assert enroll(scenario, ack_again)["local_enrollment_complete"]
    assert ledger.audit_counts()["issued"] == 1
    # Successful test-only rollback replay is NOT production admission.
    assert receipt_check(scenario, ack_again)["can_dispatch"] is False


def test_operator_acknowledgement_lost_after_issue_is_not_safe_to_retry(
    scenario
):
    issuer, ledger, _, req, _ = scenario

    def issue_and_drop_reply():
        issuer.issue(req, now_ms=NOW)
        raise TimeoutError("test transport lost response")

    with pytest.raises(TimeoutError, match="lost response"):
        issue_and_drop_reply()
    assert issuer.audit() == {"issued": 1, "highwater": 1}
    with pytest.raises(SyntheticIssuerHold, match="issuer_stale_or_competing_sequence"):
        issuer.issue(req, now_ms=NOW)
    assert ledger.audit_counts()["issued"] == 0


def test_missing_issuer_receipt_key_pin_denies_enrollment(scenario):
    issuer, ledger, _, req, _ = scenario
    response = issuer.issue(req, now_ms=NOW)
    with pytest.raises(SyntheticIssuerHold, match="independent_issuer_key_pin_missing"):
        enroll(scenario, response, issuer_pin="")
    assert ledger.audit_counts()["issued"] == 0


def test_missing_operator_approval_pin_denies_receipt(scenario):
    issuer, ledger, _, req, _ = scenario
    response = issuer.issue(req, now_ms=NOW)
    with pytest.raises(SyntheticIssuerHold, match="independent_operator_key_pin_missing"):
        enroll(scenario, response, operator_pin="")
    assert ledger.audit_counts()["issued"] == 0
