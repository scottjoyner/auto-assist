#!/usr/bin/env python3
"""Offline read-only verifier of a previously persisted synthetic outbox.

Closes #396's post-crash readback *authentication* gap: locally consistent
bundle SHA + issuance rows are not enough. Uses independent caller-pinned
roots, precommit bundle hash, previous head hash and actual current source
readback. No issuance, signing, dispatch, retry or release.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
from functools import cache
from pathlib import Path
from typing import Any

_HASH = re.compile(r"^[0-9a-f]{64}$")


@cache
def _source(file: str) -> Any:
    path = Path(__file__).with_name(file)
    spec = importlib.util.spec_from_file_location(
        "recovered_outbox_" + file.replace("-", "_").replace(".", "_"), path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic_verifier_dependency_missing")
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    return instance


def inspect(
    member: Path, *, witness_id: str, seq: int, resource: Path,
    anchor: Path, artifact: Path, expected: Any,
    pinned_bundle_sha256: Any, pinned_previous_head_hash: Any,
    pinned_effect_root: Any, pinned_witness_root: Any,
    challenge: Any, now_ms: Any,
) -> dict[str, Any]:
    """Conservative observational verdict; never authorizes execution."""
    response: dict[str, Any] = {
        "schema_version": 1,
        "retry_authorized": False,
        "release_authorized": False,
        "verdict": "UNTRUSTED_RECOVERY_NO_GO",
    }

    def deny(label: str) -> dict[str, Any]:
        response["verdict"] = label
        return response

    outbox = _source("research-atomic-effect-bundle-outbox.py")
    verifier = _source("research-verify-signed-effect-bundle.py")
    local = verifier._local()
    if (
        type(seq) is not int or seq <= 1
        or not isinstance(member, Path) or not outbox._member()._safe_path(member, witness_id)
        or not local._valid_expected(expected) or expected["commit_seq"] != seq
    ):
        return deny("RECOVERY_EXPECTATION_OR_SCOPE_INVALID_NO_GO")
    if (
        type(pinned_bundle_sha256) is not str
        or _HASH.fullmatch(pinned_bundle_sha256) is None
        or type(pinned_previous_head_hash) is not str
        or _HASH.fullmatch(pinned_previous_head_hash) is None
        or type(pinned_effect_root) is not bytes or len(pinned_effect_root) != 32
        or type(pinned_witness_root) is not bytes or len(pinned_witness_root) != 32
        or type(challenge) is not str or _HASH.fullmatch(challenge) is None
        or type(now_ms) is not int or now_ms <= 0
    ):
        return deny("INDEPENDENT_RECOVERY_PINS_MISSING_NO_GO")

    first = outbox.readback(member, witness_id=witness_id, seq=seq)
    if first["verdict"] != "COMPLETE_SIGNED_BUNDLE_OBSERVED_HOLD":
        # A previous full coherent DB snapshot restore looks exactly like
        # never-committed. This label is NEVER permission to reissue.
        response["verdict"] = first["verdict"]
        return response
    bundle = first.get("bundle")
    if type(bundle) is not dict:
        return deny("RECOVERY_BUNDLE_MALFORMED_NO_GO")
    try:
        bundle_hash = hashlib.sha256(outbox._encoded(bundle).encode("ascii")).hexdigest()
        cert = bundle["witness_certificate"]
        head = cert["tip"]
        if bundle_hash != pinned_bundle_sha256:
            return deny("RECOVERY_PINNED_BUNDLE_DIGEST_CONFLICT_NO_GO")
        if (
            head["prev_hash"] != pinned_previous_head_hash
            or head["commit_seq"] != seq
            or head["term"] != expected["term"]
            or head["last_nonce"] != expected["nonce"]
            or head["receipt_digest"] != expected["receipt_digest"]
            or cert["challenge"] != challenge
        ):
            return deny("RECOVERY_PINNED_HEAD_OR_RECEIPT_CONFLICT_NO_GO")
    except (KeyError, AttributeError, TypeError, ValueError, UnicodeError):
        return deny("RECOVERY_BUNDLE_MALFORMED_NO_GO")

    # Unlike #396 readback, verify the real Ed25519 signatures AND the
    # current synthetic artifact and resource/anchor state via #395.
    signed_state = verifier.verify(
        resource, anchor, artifact,
        expected=expected, bundle=bundle,
        pinned_effect_root=pinned_effect_root,
        pinned_witness_root=pinned_witness_root,
        expected_witness_id=witness_id,
        expected_challenge=challenge, now_ms=now_ms,
    )
    if signed_state != (True, "RESEARCH_CRYPTOGRAPHIC_EFFECT_BUNDLE_ONLY_HOLD"):
        return deny("RECOVERY_SIGNED_EFFECT_OR_RESOURCE_DENIED_NO_GO")

    second = outbox.readback(member, witness_id=witness_id, seq=seq)
    if second != first:
        return deny("RECOVERY_OUTBOX_CHANGED_DURING_READBACK_NO_GO")
    return deny("RECOVERY_CROSS_SIGNED_BUNDLE_OBSERVED_HOLD")


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_RECOVERY_DISPATCH_OR_RELEASE_ENTRYPOINT")
