#!/usr/bin/env python3
"""Public-key-only synthetic resource-effect / witness-certificate binding.

Combines #394's fsynced bytes with TWO signatures covering the same effect
and same witness receipt. NO independent signing/root custody, no real
resource owner or immutable effect proof, no production authorization.
Import-only read-only verifier; it NEVER signs, commits or retries.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.util
import json
import os
from functools import cache
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_EFFECT_DOMAIN = b"assistx-synthetic-effect-attestation/v1\x00"
_BIND_DOMAIN = b"assistx-synthetic-witness-effect-binding/v1\x00"
_KEY_ID = "synthetic-effect-owner-key"
_EPOCH = 4
_CLAIM = {
    "schema_version", "audience", "resource_id", "term", "commit_seq",
    "generation", "checksum", "revision_sha256", "receipt_digest",
    "artifact_sha256", "nonce", "challenge", "key_epoch",
    "issued_at_ms", "expires_at_ms",
}
_BIND = {
    "schema_version", "audience", "witness_id", "resource_id", "term",
    "commit_seq", "nonce", "challenge", "artifact_sha256",
    "effect_attestation_sha256", "witness_certificate_sha256",
    "key_epoch", "issued_at_ms", "expires_at_ms",
}
_RECEIPT_CERT = {
    "schema_version", "witness_id", "key_epoch", "challenge",
    "issued_at_ms", "expires_at_ms", "tip", "signature",
}
_ENVELOPE = {
    "effect_attestation", "witness_certificate", "witness_binding",
}


def canonical(domain: bytes, data: dict[str, Any]) -> bytes:
    return domain + json.dumps(
        data, separators=(",", ":"), sort_keys=True,
        allow_nan=False, ensure_ascii=True,
    ).encode("ascii")


@cache
def _local() -> Any:
    p = Path(__file__).with_name("research-terminal-receipt-readback.py")
    spec = importlib.util.spec_from_file_location("synthetic_effect_terminal", p)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic_research_reader_unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@cache
def _artifact() -> Any:
    p = Path(__file__).with_name("research-effect-artifact-gate.py")
    spec = importlib.util.spec_from_file_location("synthetic_effect_artifact", p)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic_research_artifact_unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@cache
def _quorum() -> Any:
    p = Path(__file__).with_name("research-quorum-highwater-tip.py")
    spec = importlib.util.spec_from_file_location("synthetic_effect_quorum", p)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic_research_quorum_unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _signature(pubkey: Any, signature: Any, message: bytes) -> bool:
    if (
        type(pubkey) is not bytes or len(pubkey) != 32
        or type(signature) is not str or len(signature) > 128
    ):
        return False
    try:
        value = base64.b64decode(signature, validate=True)
        if len(value) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(pubkey).verify(value, message)
        return True
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False


def _fresh(record: dict[str, Any], epoch: int, challenge: str, now_ms: int) -> bool:
    return (
        type(record.get("key_epoch")) is int and record["key_epoch"] == epoch
        and record.get("challenge") == challenge
        and type(record.get("issued_at_ms")) is int
        and type(record.get("expires_at_ms")) is int
        and 0 < record["issued_at_ms"] <= now_ms < record["expires_at_ms"]
        and record["expires_at_ms"] - record["issued_at_ms"] <= 60_000
    )


def verify(
    resource: Path, anchor: Path, artifact_path: Path, *,
    expected: Any, bundle: Any, pinned_effect_root: Any,
    pinned_witness_root: Any, expected_witness_id: str,
    expected_challenge: str, now_ms: int,
    pinned_effect_epoch: int = _EPOCH, pinned_witness_epoch: int = 3,
) -> tuple[bool, str]:
    """Read-only research verification, never production release approval."""
    local = _local()
    art = _artifact()
    q = _quorum()
    if not local._valid_expected(expected):
        return False, "EXPECTED_RESOURCE_RECEIPT_INVALID_NO_GO"
    if (
        type(pinned_effect_root) is not bytes or len(pinned_effect_root) != 32
        or type(pinned_witness_root) is not bytes or len(pinned_witness_root) != 32
        or type(expected_challenge) is not str or not q._sha(expected_challenge)
        or type(now_ms) is not int or now_ms < 1
        or type(pinned_effect_epoch) is not int or pinned_effect_epoch < 1
        or type(pinned_witness_epoch) is not int or pinned_witness_epoch < 1
        or type(expected_witness_id) is not str or not expected_witness_id
    ):
        return False, "INDEPENDENT_ROOT_OR_CHALLENGE_MISSING_NO_GO"
    if type(bundle) is not dict or set(bundle) != _ENVELOPE:
        return False, "EFFECT_BUNDLE_ENVELOPE_INVALID_NO_GO"
    effect = bundle["effect_attestation"]
    member = bundle["witness_certificate"]
    binding = bundle["witness_binding"]
    if (
        type(effect) is not dict or set(effect) != {"key_id", "claim", "signature"}
        or effect["key_id"] != _KEY_ID
        or type(effect["claim"]) is not dict or set(effect["claim"]) != _CLAIM
        or type(member) is not dict or set(member) != _RECEIPT_CERT
        or type(binding) is not dict or set(binding) != {"claim", "signature"}
        or type(binding["claim"]) is not dict or set(binding["claim"]) != _BIND
    ):
        return False, "EFFECT_BUNDLE_CONTRACT_INVALID_NO_GO"
    e = effect["claim"]
    b = binding["claim"]
    if (
        e["schema_version"] != 1 or type(e["schema_version"]) is not int
        or e["audience"] != "synthetic-committed-resource-effect"
        or b["schema_version"] != 1 or type(b["schema_version"]) is not int
        or b["audience"] != "synthetic-witness-effect-binding"
        or not _fresh(e, pinned_effect_epoch, expected_challenge, now_ms)
        or not _fresh(b, pinned_witness_epoch, expected_challenge, now_ms)
        or not _fresh(member, pinned_witness_epoch, expected_challenge, now_ms)
    ):
        return False, "EFFECT_BUNDLE_CONTEXT_OR_FRESHNESS_DENIED_NO_GO"
    try:
        exact_effect = {
            "resource_id": expected["resource_id"], "term": expected["term"],
            "commit_seq": expected["commit_seq"], "generation": expected["generation"],
            "checksum": expected["checksum"], "revision_sha256": expected["revision_sha256"],
            "receipt_digest": expected["receipt_digest"], "nonce": expected["nonce"],
        }
        if any(e[k] != v or type(e[k]) is not type(v) for k, v in exact_effect.items()):
            return False, "EFFECT_CLAIM_RESOURCE_MISMATCH_NO_GO"
        if not q._sha(e["artifact_sha256"]):
            return False, "EFFECT_DIGEST_INVALID_NO_GO"
        if (
            member["witness_id"] != expected_witness_id
            or member["schema_version"] != 1
            or type(member["schema_version"]) is not int
            or member["tip"]["resource_id"] != expected["resource_id"]
            or member["tip"]["term"] != expected["term"]
            or member["tip"]["commit_seq"] != expected["commit_seq"]
            or member["tip"]["last_nonce"] != expected["nonce"]
            or member["tip"]["receipt_digest"] != expected["receipt_digest"]
            or not q.valid_tip(member["tip"])
        ):
            return False, "WITNESS_CERTIFICATE_RECEIPT_MISMATCH_NO_GO"
        unsigned_member = {k: v for k, v in member.items() if k != "signature"}
        if not _signature(
            pinned_witness_root, member["signature"],
            q.canonical(unsigned_member),
        ):
            return False, "WITNESS_CERTIFICATE_SIGNATURE_INVALID_NO_GO"
        if not _signature(
            pinned_effect_root, effect["signature"],
            canonical(_EFFECT_DOMAIN, e),
        ):
            return False, "RESOURCE_EFFECT_SIGNATURE_INVALID_NO_GO"
        effect_hash = hashlib.sha256(canonical(_EFFECT_DOMAIN, e)).hexdigest()
        member_hash = hashlib.sha256(q.canonical(unsigned_member)).hexdigest()
        required_bind = {
            "witness_id": expected_witness_id,
            "resource_id": expected["resource_id"], "term": expected["term"],
            "commit_seq": expected["commit_seq"], "nonce": expected["nonce"],
            "challenge": expected_challenge, "artifact_sha256": e["artifact_sha256"],
            "effect_attestation_sha256": effect_hash,
            "witness_certificate_sha256": member_hash,
        }
        if any(b[k] != v or type(b[k]) is not type(v) for k, v in required_bind.items()):
            return False, "SIGNED_EFFECT_WITNESS_JOIN_MISMATCH_NO_GO"
        if not _signature(
            pinned_witness_root, binding["signature"],
            canonical(_BIND_DOMAIN, b),
        ):
            return False, "WITNESS_EFFECT_BINDING_SIGNATURE_INVALID_NO_GO"
    except (KeyError, TypeError, ValueError, AttributeError):
        return False, "EFFECT_BUNDLE_MALFORMED_CONTENT_NO_GO"
    if not art._valid_path(artifact_path):
        return False, "EFFECT_ARTIFACT_SCOPE_DENIED_NO_GO"
    try:
        fd = os.open(artifact_path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False, "EFFECT_ARTIFACT_ABSENT_NO_GO"
    try:
        if not art._read_pinned(fd, artifact_path, e["artifact_sha256"]):
            return False, "SIGNED_EFFECT_ARTIFACT_MISMATCH_NO_GO"
        local_verdict = local.assess(resource, anchor, expected=expected)
        if local_verdict["verdict"] != "ANCHOR_CONSUMED_OBSERVED_HOLD":
            return False, "RESOURCE_OR_ANCHOR_NOT_TERMINAL_NO_GO"
        if not art._read_pinned(fd, artifact_path, e["artifact_sha256"]):
            return False, "EFFECT_CHANGED_DURING_VERIFICATION_NO_GO"
        return True, "RESEARCH_CRYPTOGRAPHIC_EFFECT_BUNDLE_ONLY_HOLD"
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_RESOURCE_OWNER_OR_RELEASE_ENTRYPOINT")
