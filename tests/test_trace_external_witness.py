"""Receiver-controlled signing contract; no production keys, no NAS writes."""

from __future__ import annotations

import copy
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_trace_segment_plan import history as huge_history  # noqa: F401 -- pytest fixture

from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_external_witness import (
    GENESIS,
    attest_restored_archive,
    receipt_digest,
    verify_external_receipt,
)
from assistx.trace_segment_bundle import stage_bundle
from assistx.trace_segment_plan import digest


@pytest.fixture(scope="module")
def custody(tmp_path_factory, huge_history):  # noqa: F811 -- injected pytest fixture
    root = tmp_path_factory.mktemp("receiver-root")
    passphrase = root / "passphrase"
    passphrase.write_bytes(b"fixture-only-32-character-custody-secret-unsigned")
    passphrase.chmod(0o600)
    signing_key = b"T" * 48
    node_root = root / "xwing"
    node_root.mkdir(mode=0o700)
    journal_sha = digest(huge_history)
    archive = node_root / journal_sha
    archive.mkdir(mode=0o700)
    stage_bundle(
        huge_history,
        root=archive,
        node_id="xwing",
        passphrase=passphrase,
        signing_key=signing_key,
        max_segment_bytes=1024 * 1024,
    )
    index_sha = digest((archive / "index.json").read_bytes())
    witness_signer = Ed25519PrivateKey.generate()
    receipt = attest_restored_archive(
        archive=archive,
        node_id="xwing",
        signing_key=signing_key,
        passphrase=passphrase,
        witness_signer=witness_signer,
        witness_id="independent-custodian-1",
        sequence=1,
        previous_receipt_sha256=GENESIS,
        observed_at_ms=1_800_000_000_000,
        expected_journal_sha256=journal_sha,
        expected_index_sha256=index_sha,
    )
    return {
        "root": root,
        "archive": archive,
        "passphrase": passphrase,
        "signing_key": signing_key,
        "journal_sha": journal_sha,
        "index_sha": index_sha,
        "signer": witness_signer,
        "receipt": receipt,
    }


def accepted(custody, receipt=None, **overrides):
    config = {
        "witness_verifier": custody["signer"].public_key(),
        "witness_id": "independent-custodian-1",
        "node_id": "xwing",
        "journal_sha256": custody["journal_sha"],
        "index_sha256": custody["index_sha"],
        "expected_sequence": 1,
        "expected_previous_receipt_sha256": GENESIS,
        "minimum_observed_at_ms": 1_800_000_000_000,
    }
    config.update(overrides)
    return verify_external_receipt(receipt or custody["receipt"], **config)


def test_external_signature_issued_only_after_full_restore(custody):
    assert custody["receipt"]["records"] == 950
    assert custody["receipt"]["journal_sha256"] == custody["journal_sha"]
    assert accepted(custody) == receipt_digest(custody["receipt"])


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("witness_id", "fake-custodian"),
        ("node_id", "scotts-macbook-air"),
        ("journal_sha256", "f" * 64),
        ("index_sha256", "f" * 64),
    ],
)
def test_expected_identity_binding_denies(custody, field, wrong):
    with pytest.raises(TraceDenied, match="witness_binding_mismatch"):
        accepted(custody, **{field: wrong})


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("expected_sequence", 2),
        ("expected_previous_receipt_sha256", "a" * 64),
        ("minimum_observed_at_ms", 1_800_000_000_001),
    ],
)
def test_independent_anchor_denies_replay(custody, field, wrong):
    with pytest.raises(TraceDenied, match="witness_replay_or_rollback"):
        accepted(custody, **{field: wrong})


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("records", 951),
        ("journal_last_hash", "f" * 64),
        ("observed_at_ms", 1_900_000_000_000),
    ],
)
def test_receipt_payload_tamper_denied(custody, field, wrong):
    corrupt = copy.deepcopy(custody["receipt"])
    corrupt[field] = wrong
    with pytest.raises(TraceDenied, match="witness_signature_invalid"):
        accepted(custody, receipt=corrupt)


def test_foreign_signer_and_invalid_signature_denied(custody):
    with pytest.raises(TraceDenied, match="witness_signature_invalid"):
        accepted(custody, witness_verifier=Ed25519PrivateKey.generate().public_key())
    bad = {**custody["receipt"], "signature": "X"}
    with pytest.raises(TraceDenied, match="witness_signature_invalid"):
        accepted(custody, receipt=bad)


def test_strict_receipt_schema_denies_extra_field(custody):
    forged = {**custody["receipt"], "extra_production_authority": True}
    with pytest.raises(TraceDenied, match="witness_schema_invalid"):
        accepted(custody, receipt=forged)


def test_second_receipt_requires_previous_external_head(custody):
    first_digest = accepted(custody)
    next_receipt = attest_restored_archive(
        archive=custody["archive"],
        node_id="xwing",
        signing_key=custody["signing_key"],
        passphrase=custody["passphrase"],
        witness_signer=custody["signer"],
        witness_id="independent-custodian-1",
        sequence=2,
        previous_receipt_sha256=first_digest,
        observed_at_ms=1_800_000_001_000,
        expected_journal_sha256=custody["journal_sha"],
        expected_index_sha256=custody["index_sha"],
    )
    assert accepted(
        custody,
        receipt=next_receipt,
        expected_sequence=2,
        expected_previous_receipt_sha256=first_digest,
    ) == receipt_digest(next_receipt)
    with pytest.raises(TraceDenied, match="witness_replay_or_rollback"):
        accepted(custody, receipt=next_receipt, expected_sequence=1)


def test_wrong_encryption_secret_prevents_receipt_issuance(custody, tmp_path):
    invalid = tmp_path / "secret"
    invalid.write_bytes(b"fixture-other-32-character-encryption-secret")
    invalid.chmod(0o600)
    with pytest.raises(TraceDenied):
        attest_restored_archive(
            archive=custody["archive"],
            node_id="xwing",
            signing_key=custody["signing_key"],
            passphrase=invalid,
            witness_signer=custody["signer"],
            witness_id="independent-custodian-1",
            sequence=1,
            previous_receipt_sha256=GENESIS,
            observed_at_ms=1_800_000_000_000,
            expected_journal_sha256=custody["journal_sha"],
            expected_index_sha256=custody["index_sha"],
        )


def test_tampered_index_and_namespace_prevent_issuance(custody, tmp_path):
    with pytest.raises(TraceDenied, match="witness_remote_index_mismatch"):
        attest_restored_archive(
            archive=custody["archive"],
            node_id="xwing",
            signing_key=custody["signing_key"],
            passphrase=custody["passphrase"],
            witness_signer=custody["signer"],
            witness_id="independent-custodian-1",
            sequence=1,
            previous_receipt_sha256=GENESIS,
            observed_at_ms=1_800_000_000_000,
            expected_journal_sha256=custody["journal_sha"],
            expected_index_sha256="f" * 64,
        )
    alias = tmp_path / "link"
    alias.symlink_to(custody["archive"])
    with pytest.raises(TraceDenied, match="witness_archive_symlink"):
        attest_restored_archive(
            archive=alias,
            node_id="xwing",
            signing_key=custody["signing_key"],
            passphrase=custody["passphrase"],
            witness_signer=custody["signer"],
            witness_id="independent-custodian-1",
            sequence=1,
            previous_receipt_sha256=GENESIS,
            observed_at_ms=1_800_000_000_000,
            expected_journal_sha256=custody["journal_sha"],
            expected_index_sha256=custody["index_sha"],
        )


def test_genesis_anchor_not_implicitly_trusted(custody):
    with pytest.raises(TraceDenied, match="witness_genesis_invalid"):
        attest_restored_archive(
            archive=custody["archive"],
            node_id="xwing",
            signing_key=custody["signing_key"],
            passphrase=custody["passphrase"],
            witness_signer=custody["signer"],
            witness_id="independent-custodian-1",
            sequence=1,
            previous_receipt_sha256="b" * 64,
            observed_at_ms=1_800_000_000_000,
            expected_journal_sha256=custody["journal_sha"],
            expected_index_sha256=custody["index_sha"],
        )


def test_ciphertext_substitution_is_not_signed(custody, tmp_path):
    import shutil

    shadow_root = tmp_path / "xwing"
    shadow_root.mkdir(mode=0o700)
    archive = shadow_root / custody["journal_sha"]
    shutil.copytree(custody["archive"], archive)
    chunk = next(archive.glob("segment-*.gpg"))
    with chunk.open("ab") as file:
        file.write(b"untrusted")
    with pytest.raises(TraceDenied, match="segment_ciphertext_hash_invalid"):
        attest_restored_archive(
            archive=archive,
            node_id="xwing",
            signing_key=custody["signing_key"],
            passphrase=custody["passphrase"],
            witness_signer=custody["signer"],
            witness_id="independent-custodian-1",
            sequence=1,
            previous_receipt_sha256=GENESIS,
            observed_at_ms=1_800_000_000_000,
            expected_journal_sha256=custody["journal_sha"],
            expected_index_sha256=custody["index_sha"],
        )
