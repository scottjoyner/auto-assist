"""Sweep37: the Ed25519 projection-verify branch must use the in-module verifier.

Hermetic: generated in-memory Ed25519 keys, a PEM written under pytest's tmp_path,
and pure dict documents. No network, DB, Redis, Docker, credentials or live state.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistx.recovery_snapshot import verify_projection
from assistx.runtime_projection import projection_checksum, projection_signature
from assistx.runtime_projection_v2 import signing_message

SECRET = "snapshot-secret"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _document(*, algorithm: str = "Ed25519") -> dict:
    doc = {
        "schema_version": "2",
        "source": "assistx",
        "generation": 7,
        "revision": "fleet-7",
        "generated_at_ms": 1_000,
        "expires_at_ms": 61_000,
        "signature_algorithm": algorithm,
        "signature_key_id": "assistx-runtime-projection-v1",
        "providers": [],
    }
    doc["checksum"] = projection_checksum(doc)
    return doc


def _sign_ed25519(doc: dict, key: Ed25519PrivateKey) -> None:
    doc["signature"] = _b64url(key.sign(signing_message(doc)))


def _install_public_key(tmp_path, monkeypatch, key: Ed25519PrivateKey) -> None:
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = tmp_path / "runtime-projection-verify.pem"
    path.write_bytes(pem)
    monkeypatch.setenv("ASSISTX_RUNTIME_PROJECTION_VERIFY_KEY_FILE", str(path))


def test_valid_ed25519_projection_verifies(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    _install_public_key(tmp_path, monkeypatch, key)
    doc = _document()
    _sign_ed25519(doc, key)

    assert verify_projection(doc, SECRET) is None


def test_ed25519_bad_signature_is_rejected(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    _install_public_key(tmp_path, monkeypatch, key)
    doc = _document()
    _sign_ed25519(doc, key)
    # generated_at_ms is in the signed message but excluded from the checksum,
    # so this reaches Ed25519 verification with a valid checksum.
    doc["generated_at_ms"] += 1

    with pytest.raises(ValueError, match="Ed25519"):
        verify_projection(doc, SECRET)


def test_missing_verify_key_fails_closed(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    _install_public_key(tmp_path, monkeypatch, key)
    doc = _document()
    _sign_ed25519(doc, key)
    monkeypatch.setenv(
        "ASSISTX_RUNTIME_PROJECTION_VERIFY_KEY_FILE",
        str(tmp_path / "absent.pem"),
    )

    with pytest.raises(ValueError, match="Ed25519"):
        verify_projection(doc, SECRET)


def test_legacy_bridge_resigns_verified_ed25519_document(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    _install_public_key(tmp_path, monkeypatch, key)
    doc = _document()
    _sign_ed25519(doc, key)
    original_signature = doc["signature"]
    monkeypatch.setenv("ASSISTX_REPLICATION_LEGACY_BRIDGE", "true")

    assert verify_projection(doc, SECRET) is None
    assert doc["signature"] != original_signature
    expected = projection_signature(
        doc["generation"],
        doc["checksum"],
        doc["generated_at_ms"],
        doc["expires_at_ms"],
        SECRET,
    )
    assert doc["signature"] == expected


def test_tampered_checksum_is_rejected_before_signature_check(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    _install_public_key(tmp_path, monkeypatch, key)
    doc = _document()
    _sign_ed25519(doc, key)
    doc["generation"] = 999

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_projection(doc, SECRET)


def test_legacy_hmac_path_still_verifies():
    doc = _document(algorithm="")
    doc["signature"] = projection_signature(
        doc["generation"],
        doc["checksum"],
        doc["generated_at_ms"],
        doc["expires_at_ms"],
        SECRET,
    )

    assert verify_projection(doc, SECRET) is None
