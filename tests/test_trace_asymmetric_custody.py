"""End-to-end distinct Ed25519 producer and independent receiver keys."""

from __future__ import annotations

import copy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_trace_producer_manifest import custody as producer_fixture  # noqa: F401 -- injected fixture
from test_trace_segment_plan import history as huge_history  # noqa: F401 -- transitive fixture

from assistx.trace_asymmetric_custody import (
    GENESIS,
    attest_with_producer_public_key,
    receipt_digest,
    verify_dual_authority_receipt,
)
from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_producer_manifest import manifest_digest


@pytest.fixture(scope="module")
def two_keys(producer_fixture):  # noqa: F811 -- injected pytest fixture
    witness_private = Ed25519PrivateKey.generate()
    assert witness_private.public_key() != producer_fixture["signer"].public_key()
    kw = dict(
        archive=producer_fixture["archive"],
        producer_manifest=producer_fixture["manifest"],
        producer_verifier=producer_fixture["signer"].public_key(),
        decryption_passphrase=producer_fixture["secret"],
        producer_id="controller-a",
        producer_key_id="manifest-key-2026-a",
        node_id="xwing",
        expected_journal_sha256=producer_fixture["journal_sha"],
        expected_index_sha256=producer_fixture["index_sha"],
        expected_manifest_generation=1,
        expected_previous_manifest_sha256=GENESIS,
        minimum_manifest_issued_at_ms=1_800_100_000_000,
        witness_signer=witness_private,
        witness_id="independent-custodian-1",
        receipt_sequence=1,
        previous_receipt_sha256=GENESIS,
        observed_at_ms=1_800_100_001_000,
    )
    receipt = attest_with_producer_public_key(**kw)
    return dict(producer=producer_fixture, witness=witness_private, receipt=receipt, kwargs=kw)


def check(c, receipt=None, **overrides):
    options = dict(
        witness_verifier=c["witness"].public_key(),
        witness_id="independent-custodian-1",
        producer_id="controller-a",
        producer_key_id="manifest-key-2026-a",
        expected_producer_manifest_sha256=manifest_digest(c["producer"]["manifest"]),
        node_id="xwing",
        expected_journal_sha256=c["producer"]["journal_sha"],
        expected_index_sha256=c["producer"]["index_sha"],
        expected_sequence=1,
        expected_previous_receipt_sha256=GENESIS,
        minimum_observed_at_ms=1_800_100_001_000,
    )
    options.update(overrides)
    return verify_dual_authority_receipt(receipt or c["receipt"], **options)


def test_two_disjoint_signing_keys_complete_public_only_chain(two_keys):
    assert two_keys["receipt"]["records"] == 950
    assert two_keys["receipt"]["producer_manifest_sha256"] == manifest_digest(two_keys["producer"]["manifest"])
    assert check(two_keys) == receipt_digest(two_keys["receipt"])


@pytest.mark.parametrize(
    ("name", "changed", "error"),
    [
        ("producer_id", "evil-producer", "dual_custody_binding_mismatch"),
        ("producer_key_id", "wrong-key", "dual_custody_binding_mismatch"),
        ("witness_id", "wrong-receiver", "dual_custody_binding_mismatch"),
        ("node_id", "scotts-macbook-air", "dual_custody_binding_mismatch"),
        ("expected_producer_manifest_sha256", "e" * 64, "dual_custody_binding_mismatch"),
        ("expected_sequence", 2, "dual_custody_replay_or_rollback"),
        ("expected_previous_receipt_sha256", "e" * 64, "dual_custody_replay_or_rollback"),
        ("minimum_observed_at_ms", 1_800_100_001_001, "dual_custody_replay_or_rollback"),
    ],
)
def test_independent_verifier_pin_mismatch_denied(two_keys, name, changed, error):
    with pytest.raises(TraceDenied, match=error):
        check(two_keys, **{name: changed})


def test_witness_signature_mutation_and_forged_key_denied(two_keys):
    altered = copy.deepcopy(two_keys["receipt"])
    altered["producer_manifest_sha256"] = "a" * 64
    with pytest.raises(TraceDenied):
        check(two_keys, altered)
    with pytest.raises(TraceDenied, match="dual_custody_signature_invalid"):
        check(two_keys, witness_verifier=Ed25519PrivateKey.generate().public_key())


def test_wrong_producer_key_denies_receipt_issuance(two_keys):
    params = {**two_keys["kwargs"], "producer_verifier": Ed25519PrivateKey.generate().public_key()}
    with pytest.raises(TraceDenied, match="producer_manifest_signature_invalid"):
        attest_with_producer_public_key(**params)


def test_no_receiver_receipt_on_producer_manifest_substitution(two_keys):
    changed = copy.deepcopy(two_keys["kwargs"]["producer_manifest"])
    changed["records"] += 1
    params = {**two_keys["kwargs"], "producer_manifest": changed}
    with pytest.raises(TraceDenied, match="producer_manifest_signature_invalid"):
        attest_with_producer_public_key(**params)


def test_two_receipts_are_chain_linked_to_receiver_high_water(two_keys):
    prior = receipt_digest(two_keys["receipt"])
    params = {
        **two_keys["kwargs"],
        "receipt_sequence": 2,
        "previous_receipt_sha256": prior,
        "observed_at_ms": 1_800_100_002_000,
    }
    second = attest_with_producer_public_key(**params)
    assert check(
        two_keys,
        second,
        expected_sequence=2,
        expected_previous_receipt_sha256=prior,
    ) == receipt_digest(second)
    with pytest.raises(TraceDenied, match="dual_custody_replay_or_rollback"):
        check(two_keys, second)


def test_refuses_unknown_receipt_fields(two_keys):
    forged = {**two_keys["receipt"], "permit_shell": True}
    with pytest.raises(TraceDenied, match="dual_custody_schema_invalid"):
        check(two_keys, forged)
