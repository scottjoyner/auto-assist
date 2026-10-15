#!/usr/bin/env python3
"""Deny-only offline convergence witness experiment; NEVER a release GO.

Even two valid signatures can describe a fictional commit. Acceptable research
consistency requires an independently governed, *fresh* resource readback and
an externally held monotonic high-water anchor. This function cannot prove
the callback or trust root is actually independent; operator review is still
mandatory. No network, files, writes, retries or production actions.
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

_DOMAIN = b"assistx-convergence-independent-witness/v1\x00"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_CLAIM_KEYS = frozenset({
    "schema_version", "audience", "resource_id", "generation",
    "checksum", "revision_sha256", "evidence_sha256", "term",
    "commit_seq", "nonce", "issued_at_ms", "expires_at_ms",
})
_RESOURCE_KEYS = frozenset({
    "resource_id", "generation", "checksum", "revision_sha256",
    "term", "commit_seq", "committed",
})
_EXPECTED_KEYS = frozenset({
    "resource_id", "generation", "checksum", "revision_sha256",
    "evidence_sha256",
})
_WATERMARK_KEYS = frozenset({"term", "commit_seq", "last_nonce"})
_MAX_MS = 2**63 - 1


def _positive(value: Any) -> bool:
    return type(value) is int and 0 < value <= _MAX_MS


def _valid_claim(claim: Any) -> bool:
    return (
        isinstance(claim, dict)
        and set(claim) == _CLAIM_KEYS
        and claim.get("schema_version") == 1
        and claim.get("audience") == "convergence-evidence-research"
        and claim.get("resource_id") == "auto-router-runtime-projection"
        and all(_positive(claim.get(k)) for k in (
            "generation", "term", "commit_seq", "issued_at_ms", "expires_at_ms",
        ))
        and all(isinstance(claim.get(k), str) and _HEX64.fullmatch(claim[k])
                for k in ("checksum", "revision_sha256", "evidence_sha256"))
        and isinstance(claim.get("nonce"), str)
        and _NONCE.fullmatch(claim["nonce"]) is not None
    )


def _canonical_claim(claim: dict[str, Any]) -> bytes:
    return _DOMAIN + json.dumps(
        claim, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _readback_shape(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == _RESOURCE_KEYS
        and value.get("resource_id") == "auto-router-runtime-projection"
        and value.get("committed") is True
        and all(_positive(value.get(k)) for k in ("generation", "term", "commit_seq"))
        and all(isinstance(value.get(k), str) and _HEX64.fullmatch(value[k])
                for k in ("checksum", "revision_sha256"))
    )


def _expected_shape(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == _EXPECTED_KEYS
        and value.get("resource_id") == "auto-router-runtime-projection"
        and _positive(value.get("generation"))
        and all(isinstance(value.get(k), str) and _HEX64.fullmatch(value[k])
                for k in ("checksum", "revision_sha256", "evidence_sha256"))
    )


def _anchor_shape(value: Any) -> bool:
    return (
        isinstance(value, dict) and set(value) == _WATERMARK_KEYS
        and all(_positive(value.get(k)) for k in ("term", "commit_seq"))
        and isinstance(value.get("last_nonce"), str)
        and _NONCE.fullmatch(value["last_nonce"]) is not None
    )

def evaluate(
    envelope: Any,
    *,
    trust_roots: Mapping[str, bytes],
    expected_evidence: Any,
    external_highwater: Any,
    readback: Callable[[], Any] | None,
    now_ms: int,
) -> tuple[bool, str]:
    """Return *research consistency*, never runtime/production admission.

    The caller must establish outside this process that both the readback
    source and the high-water anchor are independently governed, current,
    and unavailable to the signer. Passing untrusted values for either can
    produce a false consistency result; no trust establishment occurs here.
    """
    if not callable(readback):
        return False, "independent_resource_readback_missing"
    if not _expected_shape(expected_evidence):
        return False, "independent_expected_evidence_invalid"
    if not _anchor_shape(external_highwater):
        return False, "external_highwater_missing_or_invalid"
    if not _positive(now_ms):
        return False, "verification_clock_invalid"
    if not isinstance(envelope, dict) or set(envelope) != {
        "claim", "key_id", "signature",
    }:
        return False, "witness_envelope_invalid"
    claim = envelope.get("claim")
    if not _valid_claim(claim):
        return False, "witness_claim_invalid"
    if (
        claim["issued_at_ms"] > now_ms
        or now_ms >= claim["expires_at_ms"]
        or claim["expires_at_ms"] - claim["issued_at_ms"] > 300_000
    ):
        return False, "witness_expired_or_untrusted_time"
    key_id = envelope.get("key_id")
    if not isinstance(key_id, str) or key_id != "independent-resource-v1":
        return False, "witness_key_unrecognized"
    public_bytes = trust_roots.get(key_id)
    if not isinstance(public_bytes, bytes) or len(public_bytes) != 32:
        return False, "pinned_trust_root_unavailable"
    try:
        signature = base64.b64decode(envelope["signature"], validate=True)
        if len(signature) != 64:
            return False, "witness_signature_invalid"
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            signature, _canonical_claim(claim)
        )
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False, "witness_signature_invalid"

    if any(claim[key] != expected_evidence[key] for key in _EXPECTED_KEYS):
        return False, "witness_does_not_bind_expected_artifact"
    current_position = (claim["term"], claim["commit_seq"])
    highwater_position = (
        external_highwater["term"], external_highwater["commit_seq"],
    )
    if (
        current_position <= highwater_position
        or claim["nonce"] == external_highwater["last_nonce"]
    ):
        return False, "witness_replay_or_restored_highwater"

    # Re-read after signature/anchor checks so an effect which changes
    # between a preliminary observation and finalization is denied.
    try:
        first = readback()
        second = readback()
    except Exception:
        return False, "resource_readback_unavailable"
    if not _readback_shape(first) or not _readback_shape(second):
        return False, "resource_readback_not_authoritative"
    if first != second:
        return False, "resource_effect_changed_during_readback"
    for key in _RESOURCE_KEYS - {"committed"}:
        if first[key] != claim[key]:
            return False, "witness_resource_commit_mismatch"
    return True, "RESEARCH_CONSISTENCY_ONLY"


if __name__ == "__main__":
    # There is deliberately no executable production/CLI acceptance mode.
    # Import evaluate() from disposable tests with independent fixtures.
    raise SystemExit("RESEARCH_ONLY_NO_STANDALONE_RELEASE_GATE")
