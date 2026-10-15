#!/usr/bin/env python3
"""Offline research-only Ed25519 verification of resource COMMIT evidence.

Verifier receives a PUBLIC key only, unlike #383's shared-key HMAC lab.
A database row is NOT inherently an authentic commit: a separate trusted
attestor is required to sign *after* independently verifying the effect.
This research function cannot establish that the attestor or readback is
independently governed. No private-key handling, credential, storage,
network, SQL, release or hardware access is implemented here.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_DOMAIN = b"assistx-resource-commit-asymmetric-research/v1\x00"
_KEY_ID = "independent-resource-commit-research-v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_INT_MAX = (1 << 63) - 1
_RESOURCE = "auto-router-runtime-projection"
_EXPECTED = frozenset({
    "resource_id", "term", "commit_seq", "generation",
    "checksum", "revision_sha256", "committed",
})
_RECEIPT = frozenset({
    "schema_version", "audience", "resource_id", "term", "commit_seq",
    "generation", "checksum", "revision_sha256", "nonce", "challenge",
    "key_epoch", "issued_at_ms", "expires_at_ms",
})
_ANCHOR = frozenset({"term", "commit_seq", "last_nonce"})


def _integer(value: Any) -> bool:
    return type(value) is int and 0 < value <= _INT_MAX


def _hash(value: Any) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _nonce(value: Any) -> bool:
    return isinstance(value, str) and _NONCE.fullmatch(value) is not None


def _expected(value: Any) -> bool:
    return (
        type(value) is dict and set(value) == _EXPECTED
        and value.get("resource_id") == _RESOURCE
        and value.get("committed") is True
        and all(_integer(value.get(k)) for k in ("term", "commit_seq", "generation"))
        and all(_hash(value.get(k)) for k in ("checksum", "revision_sha256"))
    )


def _anchor(value: Any) -> bool:
    return (
        type(value) is dict and set(value) == _ANCHOR
        and _integer(value.get("term")) and _integer(value.get("commit_seq"))
        and _nonce(value.get("last_nonce"))
    )


def _receipt(value: Any) -> bool:
    return (
        type(value) is dict and set(value) == _RECEIPT
        and type(value.get("schema_version")) is int
        and value["schema_version"] == 1
        and value.get("audience") == "synthetic-resource-commit-observation"
        and value.get("resource_id") == _RESOURCE
        and all(_integer(value.get(k)) for k in (
            "term", "commit_seq", "generation", "key_epoch",
            "issued_at_ms", "expires_at_ms",
        ))
        and all(_hash(value.get(k)) for k in (
            "checksum", "revision_sha256", "challenge",
        ))
        and _nonce(value.get("nonce"))
    )


def canonical(receipt: dict[str, Any]) -> bytes:
    """Domain-separated test contract, NOT a production signing interface."""
    return _DOMAIN + json.dumps(
        receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def verify(
    envelope: Any,
    *,
    pinned_public_keys: Any,
    pinned_key_epoch: Any,
    independently_expected_effect: Any,
    independently_held_anchor: Any,
    independently_issued_challenge: Any,
    readback: Callable[[], Any] | None,
    now_ms: Any,
) -> tuple[bool, str]:
    """Verify a signed report and two reads, never authorize consumption.

    Public keys, prior anchor and fresh challenge MUST be from separate,
    genuinely trusted custody. The caller's readback function could be a
    fake (including compromised DBA readback). This verifier cannot
    establish that separation or prove a post-verify effect has not moved.
    """
    if not _expected(independently_expected_effect):
        return False, "independent_effect_invalid"
    if not _anchor(independently_held_anchor):
        return False, "independent_anchor_invalid"
    if not _hash(independently_issued_challenge):
        return False, "independent_challenge_invalid"
    if not isinstance(pinned_public_keys, Mapping) or not _integer(pinned_key_epoch):
        return False, "independent_public_key_custody_missing"
    if not _integer(now_ms):
        return False, "independent_clock_invalid"
    if not callable(readback):
        return False, "authoritative_resource_readback_missing"
    if type(envelope) is not dict or set(envelope) != {"key_id", "receipt", "signature"}:
        return False, "resource_signed_envelope_invalid"
    receipt = envelope.get("receipt")
    if not _receipt(receipt):
        return False, "resource_receipt_invalid"
    if (
        receipt["issued_at_ms"] > now_ms
        or now_ms >= receipt["expires_at_ms"]
        or receipt["expires_at_ms"] - receipt["issued_at_ms"] > 60_000
    ):
        return False, "resource_receipt_expired_or_future"
    if receipt["key_epoch"] != pinned_key_epoch:
        return False, "resource_receipt_wrong_key_epoch"
    if envelope.get("key_id") != _KEY_ID:
        return False, "resource_signer_key_unrecognized"
    public = pinned_public_keys.get(_KEY_ID)
    if not isinstance(public, bytes) or len(public) != 32:
        return False, "independent_pinned_public_key_missing"
    encoded = envelope.get("signature")
    if not isinstance(encoded, str) or len(encoded) > 128:
        return False, "resource_receipt_signature_invalid"
    try:
        signature = base64.b64decode(encoded, validate=True)
        if len(signature) != 64:
            return False, "resource_receipt_signature_invalid"
        Ed25519PublicKey.from_public_bytes(public).verify(signature, canonical(receipt))
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False, "resource_receipt_signature_invalid"
    if receipt["challenge"] != independently_issued_challenge:
        return False, "resource_challenge_replay"
    if any(
        receipt[k] != independently_expected_effect[k]
        for k in ("resource_id", "term", "commit_seq", "generation", "checksum", "revision_sha256")
    ):
        return False, "signed_receipt_effect_mismatch"
    before = independently_held_anchor
    if (
        receipt["term"] != before["term"]
        or receipt["commit_seq"] != before["commit_seq"] + 1
        or receipt["nonce"] == before["last_nonce"]
    ):
        return False, "signed_receipt_not_next_monotonic_position"
    try:
        first = readback()
        second = readback()
    except Exception:
        return False, "resource_readback_unavailable"
    if not _expected(first) or not _expected(second):
        return False, "resource_readback_untrusted_shape"
    if first != second:
        return False, "resource_changed_during_readback"
    if first != independently_expected_effect:
        return False, "resource_committed_effect_not_observed"
    return True, "RESEARCH_ASYMMETRIC_OBSERVATION_ONLY"


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_RESOURCE_ADMISSION_OR_PRODUCTION_ENTRYPOINT")
