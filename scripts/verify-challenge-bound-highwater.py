#!/usr/bin/env python3
"""OFFLINE, deny-only signed high-water statement RESEARCH; never resource GO.

The verifier checks challenge freshness, a pinned external Ed25519 public key,
a domain-separated statement, and an externally known prior checkpoint.
It cannot establish the signer, signing-key custodian, checkpoint source,
callback, or administrator is genuinely independent. No network, file,
credential, TPM, database, token, production or CLI promotion actions.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_CLAIM_DOMAIN = b"assistx-research-signed-highwater/v1\x00"
_RESOURCE_DOMAIN = b"assistx-research-resource-binding/v1\x00"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_KEY_ID = "externally-pinned-anchor-research-v1"
_MAX_POSITIVE = 2**63 - 1
_EFFECT_KEYS = frozenset({
    "resource_id", "term", "commit_seq", "generation", "checksum",
    "revision_sha256", "evidence_sha256",
})
_CLAIM_KEYS = frozenset({
    "schema_version", "audience", "resource_id",
    "term", "commit_seq", "last_nonce", "challenge",
    "bound_effect_sha256", "issued_at_ms", "expires_at_ms", "key_epoch",
})
_MIN_KEYS = frozenset({"term", "commit_seq", "last_nonce"})


def _pos(value: Any) -> bool:
    return type(value) is int and 0 < value <= _MAX_POSITIVE


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _nonce(value: Any) -> bool:
    return isinstance(value, str) and _NONCE.fullmatch(value) is not None


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        allow_nan=False, ensure_ascii=True,
    ).encode("ascii")


def effect_digest(expected_effect: dict[str, Any]) -> str:
    """Hash expected evidence, not what the *attestor* claims it saw."""
    return hashlib.sha256(_RESOURCE_DOMAIN + _canonical(expected_effect)).hexdigest()


def _effect_shape(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == _EFFECT_KEYS
        and value.get("resource_id") == "auto-router-runtime-projection"
        and all(_pos(value.get(k)) for k in ("term", "commit_seq", "generation"))
        and all(_sha(value.get(k)) for k in (
            "checksum", "revision_sha256", "evidence_sha256"
        ))
    )


def _minimum_shape(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == _MIN_KEYS
        and _pos(value.get("term")) and _pos(value.get("commit_seq"))
        and _nonce(value.get("last_nonce"))
    )


def _claim_shape(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == _CLAIM_KEYS
        and type(value.get("schema_version")) is int
        and value["schema_version"] == 1
        and value.get("audience") == "research-highwater-consistency-only"
        and value.get("resource_id") == "auto-router-runtime-projection"
        and all(_pos(value.get(k)) for k in (
            "term", "commit_seq", "issued_at_ms", "expires_at_ms", "key_epoch"
        ))
        and _sha(value.get("challenge")) and _sha(value.get("bound_effect_sha256"))
        and _nonce(value.get("last_nonce"))
    )


def evaluate(
    envelope: Any,
    *,
    expected_effect: Any,
    expected_challenge: Any,
    minimum_highwater: Any,
    pinned_roots: Any,
    pinned_key_epoch: Any,
    now_ms: Any,
) -> tuple[bool, str]:
    """Validate one signed claim, never commit/readback/release authorization."""
    if not _effect_shape(expected_effect):
        return False, "expected_effect_invalid"
    if not _sha(expected_challenge):
        return False, "fresh_external_challenge_missing"
    if not _minimum_shape(minimum_highwater):
        return False, "external_minimum_checkpoint_invalid"
    if not isinstance(pinned_roots, Mapping) or not _pos(pinned_key_epoch):
        return False, "independent_key_custody_not_configured"
    if not _pos(now_ms):
        return False, "verification_clock_invalid"
    if not isinstance(envelope, dict) or set(envelope) != {
        "claim", "key_id", "signature"
    }:
        return False, "signed_statement_envelope_invalid"
    claim = envelope["claim"]
    if not _claim_shape(claim):
        return False, "signed_statement_shape_invalid"
    if (
        claim["issued_at_ms"] > now_ms
        or now_ms >= claim["expires_at_ms"]
        or claim["expires_at_ms"] - claim["issued_at_ms"] > 60_000
    ):
        return False, "signed_statement_not_fresh"
    if claim["key_epoch"] != pinned_key_epoch:
        return False, "pinned_key_epoch_mismatch"
    key_id = envelope["key_id"]
    if key_id != _KEY_ID:
        return False, "anchor_key_not_recognized"
    public = pinned_roots.get(key_id)
    if not isinstance(public, bytes) or len(public) != 32:
        return False, "external_root_unavailable"
    signature_encoded = envelope["signature"]
    if not isinstance(signature_encoded, str) or len(signature_encoded) > 128:
        return False, "signed_statement_signature_invalid"
    try:
        signature = base64.b64decode(signature_encoded, validate=True)
        if len(signature) != 64:
            return False, "signed_statement_signature_invalid"
        Ed25519PublicKey.from_public_bytes(public).verify(
            signature, _CLAIM_DOMAIN + _canonical(claim)
        )
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False, "signed_statement_signature_invalid"
    if claim["challenge"] != expected_challenge:
        return False, "challenge_replay_or_mismatch"
    if claim["bound_effect_sha256"] != effect_digest(expected_effect):
        return False, "effect_binding_mismatch"
    if (claim["term"], claim["commit_seq"]) != (
        expected_effect["term"], expected_effect["commit_seq"]
    ):
        return False, "anchor_does_not_match_resource_sequence"
    before = (minimum_highwater["term"], minimum_highwater["commit_seq"])
    after = (claim["term"], claim["commit_seq"])
    if after < before or (
        after == before and claim["last_nonce"] != minimum_highwater["last_nonce"]
    ):
        return False, "external_checkpoint_rollback_or_conflict"
    if after > before and claim["last_nonce"] == minimum_highwater["last_nonce"]:
        return False, "checkpoint_nonce_not_advanced"
    return True, "RESEARCH_SIGNED_STATEMENT_ONLY"


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_LIVE_AUTHORITY_OR_RELEASE_ENTRYPOINT")


def evaluate_strict_advance(
    envelope: Any,
    *,
    expected_effect: Any,
    expected_challenge: Any,
    minimum_highwater: Any,
    pinned_roots: Any,
    pinned_key_epoch: Any,
    now_ms: Any,
) -> tuple[bool, str]:
    """Require a *newer* signed position without claiming atomic consumption.

    This pure helper is still not a one-shot security decision. Two verifiers
    given the same external pre-advance checkpoint may both return True.
    Only an independently governed, durable CAS in the resource/anchor
    control plane can arbitrate concurrent consumption and restore.
    """
    accepted, reason = evaluate(
        envelope, expected_effect=expected_effect,
        expected_challenge=expected_challenge,
        minimum_highwater=minimum_highwater,
        pinned_roots=pinned_roots, pinned_key_epoch=pinned_key_epoch,
        now_ms=now_ms,
    )
    if not accepted:
        return False, reason
    claim = envelope["claim"]
    if (
        (claim["term"], claim["commit_seq"])
        <= (minimum_highwater["term"], minimum_highwater["commit_seq"])
    ):
        return False, "no_strict_highwater_advance"
    return True, "RESEARCH_ADVANCEMENT_UNCONSUMED_ONLY"
