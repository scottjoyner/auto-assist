"""Fail-closed synthetic, pinned-key receiver evidence research tests."""
from dataclasses import replace
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.trace_effect_reconciliation_research import (
    Classification, Operation, ReceiverReceipt,
)
from assistx.trace_signed_receiver_research import (
    DOMAIN, SCHEMA, SignedReceipt, encode_for_fixture, reconcile_signed,
)

OP = Operation("research-domain", "synthetic-op", "synthetic-effect", "x1-370", 1)
RECEIVER = "mock-receiver-one"
KEY_ID = "pre-enrolled-key-one"


@pytest.fixture
def signer():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, {KEY_ID: public}


def row(status="applied", **overrides):
    original = ReceiverReceipt(
        domain=OP.domain, operation_id=OP.operation_id,
        effect_id=OP.effect_id, owner=OP.owner, term=OP.term,
        receipt_id="synthetic-receipt-1", status=status, durable=True,
        boundary_term=1, terminal=status in (
            "applied", "aborted", "rejected-stale"),
    )
    return replace(original, **overrides)


def sign(private, value=None, *, receiver=RECEIVER, key_id=KEY_ID,
         raw_payload=None):
    payload = (encode_for_fixture(value or row(), receiver)
               if raw_payload is None else raw_payload)
    return SignedReceipt(key_id, payload, private.sign(DOMAIN + payload))


def check(operation, envelopes, keys, **overrides):
    args = dict(pinned_keys=keys, expected_receiver=RECEIVER,
                expected_key_id=KEY_ID)
    args.update(overrides)
    answer = reconcile_signed(operation, envelopes, **args)
    assert answer.automatic_replay_allowed is False
    assert answer.takeover_allowed is False
    return answer.classification


def test_authentic_mock_applied_and_historical_term(signer):
    private, keys = signer
    assert check(OP, [sign(private)], keys) is Classification.APPLIED


@pytest.mark.parametrize("status,result", [
    ("aborted", Classification.ABORTED),
    ("rejected-stale", Classification.ABORTED),
    ("unknown", Classification.UNKNOWN),
    ("not-seen", Classification.UNKNOWN),
])
def test_other_signed_receiver_outcomes_never_authorize_replay(signer, status, result):
    private, keys = signer
    assert check(OP, [sign(private, row(status))], keys) is result


def test_missing_receipt_must_remain_unknown(signer):
    _, keys = signer
    assert check(OP, [], keys) is Classification.UNKNOWN


def test_signed_receiver_reports_applied_after_fence_advanced(signer):
    private, keys = signer
    assert check(OP, [sign(private, row(boundary_term=2))], keys) is Classification.STALE_APPLIED


def test_identical_transport_repeats_are_not_second_effect(signer):
    private, keys = signer
    envelope = sign(private)
    assert check(OP, [envelope, envelope], keys) is Classification.APPLIED


def test_distinct_authenticated_application_receipts_conflict(signer):
    private, keys = signer
    assert check(OP, [sign(private), sign(private, row(receipt_id="second"))],
                 keys) is Classification.CONFLICT


def test_signed_applied_and_aborted_are_conflicting(signer):
    private, keys = signer
    assert check(OP, [sign(private), sign(private, row(
        "aborted", receipt_id="abort"))], keys) is Classification.CONFLICT


@pytest.mark.parametrize("mutation", [
    {"domain": "other-domain"}, {"operation_id": "different-op"},
    {"effect_id": "different-effect"}, {"owner": "xwing"}, {"term": 2},
])
def test_valid_signature_does_not_override_wrong_operation_binding(signer, mutation):
    private, keys = signer
    assert check(OP, [sign(private, row(**mutation))], keys) is Classification.CONFLICT


def test_signed_claim_of_durability_is_not_intrinsically_durability(signer):
    private, keys = signer
    assert check(OP, [sign(private, row(durable=False))], keys) is Classification.UNTRUSTED


def test_signed_nonterminal_application_is_untrusted(signer):
    private, keys = signer
    assert check(OP, [sign(private, row(terminal=False))], keys) is Classification.UNTRUSTED


def test_signature_tampering_fails_closed(signer):
    private, keys = signer
    original = sign(private)
    forged = replace(original, signature=bytes([original.signature[0] ^ 1])
                     + original.signature[1:])
    assert check(OP, [forged], keys) is Classification.UNTRUSTED


def test_unsigned_payload_tampering_fails_closed(signer):
    private, keys = signer
    original = sign(private)
    payload = original.payload.replace(b"synthetic-effect", b"synthetic-efFect")
    assert payload != original.payload
    assert check(OP, [replace(original, payload=payload)], keys) is Classification.UNTRUSTED


def test_wrong_signer_key_is_not_trusted(signer):
    _, keys = signer
    impostor = Ed25519PrivateKey.generate()
    assert check(OP, [sign(impostor)], keys) is Classification.UNTRUSTED


def test_signer_public_key_cannot_arrive_from_receipt(signer):
    private, keys = signer
    replacement = Ed25519PrivateKey.generate()
    wrong_key = replacement.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    assert check(OP, [sign(private)], {KEY_ID: wrong_key}) is Classification.UNTRUSTED
    assert check(OP, [sign(private, key_id="attacker-selected")],
                 keys) is Classification.UNTRUSTED


def test_signed_receiver_identity_still_must_match_external_pin(signer):
    private, keys = signer
    assert check(OP, [sign(private, receiver="alternate-receiver")],
                 keys) is Classification.UNTRUSTED


def test_missing_or_malformed_pinned_key_denied(signer):
    private, _ = signer
    assert check(OP, [sign(private)], {}) is Classification.UNTRUSTED
    assert check(OP, [sign(private)], {KEY_ID: b"x" * 31}) is Classification.UNTRUSTED


def test_wrong_expected_key_id_denied(signer):
    private, keys = signer
    assert check(OP, [sign(private)], keys,
                 expected_key_id="not-the-registered-key") is Classification.UNTRUSTED


def test_synthetically_signed_noncanonical_json_denied(signer):
    private, keys = signer
    d = json.loads(encode_for_fixture(row(), RECEIVER))
    noncanonical = json.dumps(d, indent=2, sort_keys=False).encode()
    assert check(OP, [sign(private, raw_payload=noncanonical)],
                 keys) is Classification.UNTRUSTED


def test_synthetically_signed_duplicate_json_keys_denied(signer):
    private, keys = signer
    original = encode_for_fixture(row(), RECEIVER)
    malformed = original[:-1] + b',"schema":"' + SCHEMA.encode() + b'"}'
    assert check(OP, [sign(private, raw_payload=malformed)],
                 keys) is Classification.UNTRUSTED


def test_synthetically_signed_extra_or_missing_fields_denied(signer):
    private, keys = signer
    for mutate in (lambda d: d.update({"unbound": "injection"}),
                   lambda d: d.pop("boundary_term")):
        d = json.loads(encode_for_fixture(row(), RECEIVER))
        mutate(d)
        payload = json.dumps(d, sort_keys=True, separators=(",", ":")).encode()
        assert check(OP, [sign(private, raw_payload=payload)],
                     keys) is Classification.UNTRUSTED


def test_malformed_envelope_or_batch_denied(signer):
    _, keys = signer
    assert check(OP, [None], keys) is Classification.UNTRUSTED
    assert check(OP, [SignedReceipt(KEY_ID, b"x", b"x")],
                 keys) is Classification.UNTRUSTED
    assert check(OP, (), keys) is Classification.UNKNOWN
    assert check(OP, [None] * 129, keys) is Classification.UNTRUSTED
