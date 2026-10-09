"""Producer Ed25519 envelope, receiver public-only validation and anti-replay."""

from __future__ import annotations

import copy
import json
import shutil

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_trace_segment_plan import history as huge_history  # noqa: F401 -- pytest fixture

from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_producer_manifest import (
    GENESIS,
    manifest_digest,
    sign_verified_legacy_bundle,
    verify_and_restore_public,
)
from assistx.trace_segment_bundle import stage_bundle
from assistx.trace_segment_plan import digest


@pytest.fixture(scope="module")
def custody(tmp_path_factory, huge_history):  # noqa: F811 -- pytest fixture
    root = tmp_path_factory.mktemp("producer-sign")
    parent = root / "xwing"
    parent.mkdir(mode=0o700)
    journal_sha = digest(huge_history)
    archive = parent / journal_sha
    archive.mkdir(mode=0o700)
    key = b"S" * 48
    secret = root / "fixture-passphrase"
    secret.write_bytes(b"fixture-only-unique-custody-encryption-secret-2026")
    secret.chmod(0o600)
    stage_bundle(
        huge_history,
        root=archive,
        node_id="xwing",
        passphrase=secret,
        signing_key=key,
        max_segment_bytes=1024 * 1024,
    )
    signer = Ed25519PrivateKey.generate()
    signed = sign_verified_legacy_bundle(
        archive=archive,
        node_id="xwing",
        signing_key=key,
        passphrase=secret,
        producer_signer=signer,
        producer_id="controller-a",
        key_id="manifest-key-2026-a",
        generation=1,
        previous_manifest_sha256=GENESIS,
        issued_at_ms=1_800_100_000_000,
    )
    return {
        "root": root,
        "archive": archive,
        "secret": secret,
        "key": key,
        "signer": signer,
        "manifest": signed,
        "raw": huge_history,
        "journal_sha": journal_sha,
        "index_sha": digest((archive / "index.json").read_bytes()),
    }


def verify(custody, manifest=None, **changes):
    kwargs = {
        "archive": custody["archive"],
        "manifest": manifest or custody["manifest"],
        "producer_verifier": custody["signer"].public_key(),
        "passphrase": custody["secret"],
        "producer_id": "controller-a",
        "key_id": "manifest-key-2026-a",
        "node_id": "xwing",
        "expected_journal_sha256": custody["journal_sha"],
        "expected_index_sha256": custody["index_sha"],
        "expected_generation": 1,
        "expected_previous_manifest_sha256": GENESIS,
        "minimum_issued_at_ms": 1_800_100_000_000,
    }
    kwargs.update(changes)
    return verify_and_restore_public(**kwargs)


def test_receiver_uses_only_producer_public_key_and_decryption_secret(custody):
    # No producer HMAC key is ever supplied to receiver verification.
    assert verify(custody) == custody["raw"]
    assert custody["manifest"]["records"] == 950
    assert len(custody["manifest"]["segments"]) > 8


@pytest.mark.parametrize(
    ("change", "value", "error"),
    [
        ("producer_id", "other-producer", "producer_manifest_binding_mismatch"),
        ("key_id", "untrusted-key", "producer_manifest_binding_mismatch"),
        ("node_id", "scotts-macbook-air", "producer_manifest_binding_mismatch"),
        ("expected_journal_sha256", "f" * 64, "producer_manifest_binding_mismatch"),
        ("expected_index_sha256", "f" * 64, "producer_manifest_binding_mismatch"),
        ("expected_generation", 2, "producer_manifest_replay_or_rollback"),
        ("expected_previous_manifest_sha256", "a" * 64, "producer_manifest_replay_or_rollback"),
        ("minimum_issued_at_ms", 1_800_100_000_001, "producer_manifest_replay_or_rollback"),
    ],
)
def test_pinned_producer_state_cannot_change(custody, change, value, error):
    with pytest.raises(TraceDenied, match=error):
        verify(custody, **{change: value})


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("producer_id", "attacker"),
        ("issued_at_ms", 1_900_000_000_000),
        ("records", 951),
        ("last_hash", "f" * 64),
        ("index_sha256", "f" * 64),
        ("signature", "a" * 86),
    ],
)
def test_modified_signed_metadata_fails(custody, field, bad):
    changed = copy.deepcopy(custody["manifest"])
    changed[field] = bad
    with pytest.raises(TraceDenied):
        verify(custody, changed)


def test_wrong_producer_signer_and_unknown_field_denied(custody):
    with pytest.raises(TraceDenied, match="producer_manifest_signature_invalid"):
        verify(custody, producer_verifier=Ed25519PrivateKey.generate().public_key())
    fake = {**custody["manifest"], "grant_live_shell": True}
    with pytest.raises(TraceDenied, match="producer_manifest_schema_invalid"):
        verify(custody, fake)


def test_wrong_decrypt_key_does_not_restore(custody, tmp_path):
    secret = tmp_path / "wrong"
    secret.write_bytes(b"test-only-other-encryption-passphrase-that-is-long")
    secret.chmod(0o600)
    with pytest.raises(TraceDenied):
        verify(custody, passphrase=secret)


def test_second_manifest_needs_independent_prev_head(custody):
    prev = manifest_digest(custody["manifest"])
    second = sign_verified_legacy_bundle(
        archive=custody["archive"],
        node_id="xwing",
        signing_key=custody["key"],
        passphrase=custody["secret"],
        producer_signer=custody["signer"],
        producer_id="controller-a",
        key_id="manifest-key-2026-a",
        generation=2,
        previous_manifest_sha256=prev,
        issued_at_ms=1_800_100_002_000,
    )
    assert (
        verify(
            custody,
            second,
            expected_generation=2,
            expected_previous_manifest_sha256=prev,
        )
        == custody["raw"]
    )
    with pytest.raises(TraceDenied, match="producer_manifest_replay_or_rollback"):
        verify(custody, second)


def test_genesis_and_boolean_generation_not_accepted(custody):
    with pytest.raises(TraceDenied, match="producer_manifest_genesis_invalid"):
        sign_verified_legacy_bundle(
            archive=custody["archive"],
            node_id="xwing",
            signing_key=custody["key"],
            passphrase=custody["secret"],
            producer_signer=custody["signer"],
            producer_id="controller-a",
            key_id="manifest-key-2026-a",
            generation=1,
            previous_manifest_sha256="a" * 64,
            issued_at_ms=1_800_100_000_000,
        )
    with pytest.raises(TraceDenied, match="producer_manifest_integer_invalid"):
        sign_verified_legacy_bundle(
            archive=custody["archive"],
            node_id="xwing",
            signing_key=custody["key"],
            passphrase=custody["secret"],
            producer_signer=custody["signer"],
            producer_id="controller-a",
            key_id="manifest-key-2026-a",
            generation=True,
            previous_manifest_sha256=GENESIS,
            issued_at_ms=1_800_100_000_000,
        )


def test_changed_ciphertext_and_signed_index_are_denied(custody, tmp_path):
    parent = tmp_path / "xwing"
    parent.mkdir(mode=0o700)
    archive = parent / custody["journal_sha"]
    shutil.copytree(custody["archive"], archive)
    ciphertext = next(archive.glob("segment-*.gpg"))
    with ciphertext.open("ab") as f:
        f.write(b"tampered")
    with pytest.raises(TraceDenied, match="producer_ciphertext_mismatch"):
        verify(custody, archive=archive)
    ciphertext.write_bytes((custody["archive"] / ciphertext.name).read_bytes())
    ciphertext.chmod(0o600)
    index = archive / "index.json"
    index.write_bytes(index.read_bytes() + b" ")
    with pytest.raises(TraceDenied, match="producer_legacy_index_changed"):
        verify(custody, archive=archive)


def test_metadata_segment_substitution_fails_producer_signature(custody):
    altered = copy.deepcopy(custody["manifest"])
    altered["segments"][0]["plaintext_sha256"] = "e" * 64
    with pytest.raises(TraceDenied, match="producer_manifest_signature_invalid"):
        verify(custody, altered)


def test_wrong_namespace_is_rejected(custody, tmp_path):
    unrelated = tmp_path / "other-node"
    unrelated.mkdir(mode=0o700)
    renamed = unrelated / custody["journal_sha"]
    shutil.copytree(custody["archive"], renamed)
    with pytest.raises(TraceDenied, match="producer_archive_namespace_mismatch"):
        verify(custody, archive=renamed)


def test_producer_must_sign_content_addressed_node_namespace(custody, tmp_path):
    parent = tmp_path / "untrusted-node"
    parent.mkdir(mode=0o700)
    misplaced = parent / custody["journal_sha"]
    shutil.copytree(custody["archive"], misplaced)
    with pytest.raises(TraceDenied, match="producer_archive_namespace_mismatch"):
        sign_verified_legacy_bundle(
            archive=misplaced,
            node_id="xwing",
            signing_key=custody["key"],
            passphrase=custody["secret"],
            producer_signer=custody["signer"],
            producer_id="controller-a",
            key_id="manifest-key-2026-a",
            generation=1,
            previous_manifest_sha256=GENESIS,
            issued_at_ms=1_800_100_000_000,
        )


def test_malformed_segment_data_fails_as_denial(custody):
    for malicious in (None, {"unexpected": "field"}, {"file": object()}):
        changed = copy.deepcopy(custody["manifest"])
        changed["segments"][0] = malicious
        with pytest.raises(TraceDenied):
            verify(custody, changed)
