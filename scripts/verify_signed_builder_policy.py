"""Verify operator-signed limited builder delegation and DSSE OCI statement.

Research-only. Requires externally pinned operator Ed25519 root and monotonically
maintained policy epoch. These are caller-provided, not independently authenticated.
All results explicitly HOLD, even if signatures and artifact digest match.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

try:
    from .verify_signed_image_provenance import (
        GIT_SHA, SHA256, TYPE_URI, ProvenanceHold, _exact, _json_strict,
        _read_public_file, _strict_base64, pae, verify, REPOS, _runtime_record,
    )
except ImportError:
    from verify_signed_image_provenance import (
        GIT_SHA, SHA256, TYPE_URI, ProvenanceHold, _exact, _json_strict,
        _read_public_file, _strict_base64, pae, verify, REPOS, _runtime_record,
    )

POLICY_TYPE = "application/vnd.assistx.builder-authorization+json"
SCHEMA = "assistx.builder-authorization/v1"
FIELDS = {
    "schema", "epoch", "not_before", "expires_at", "service", "source_commit",
    "manifest_sha256", "builder_id", "build_type", "builder_key_sha256",
}
MAX_POLICY_WINDOW = 86400
MAX_POLICY_EPOCH = 2**53 - 1


def _hex(v: Any, regex: re.Pattern[str], name: str) -> str:
    if not isinstance(v, str) or regex.fullmatch(v) is None:
        raise ProvenanceHold("invalid " + name)
    return v


def _signed_policy(envelope: dict[str, Any], root_key: bytes,
                   expected_fingerprint: str) -> dict[str, Any]:
    if not isinstance(root_key, bytes) or len(root_key) != 32:
        raise ProvenanceHold("invalid operator root public key length")
    if not hmac.compare_digest(hashlib.sha256(root_key).hexdigest(),
                               _hex(expected_fingerprint, SHA256, "root fingerprint")):
        raise ProvenanceHold("operator root fingerprint mismatch")
    _exact(envelope, {"payloadType", "payload", "signatures"}, "root policy envelope")
    if envelope["payloadType"] != POLICY_TYPE:
        raise ProvenanceHold("wrong signed policy domain type")
    signatures = envelope["signatures"]
    if not isinstance(signatures, list) or len(signatures) != 1:
        raise ProvenanceHold("one operator root signature required")
    sig = _exact(signatures[0], {"keyid", "sig"}, "root signature")
    if sig["keyid"] != expected_fingerprint:
        raise ProvenanceHold("untrusted root keyid")
    raw_sig = _strict_base64(sig["sig"], max_size=64)
    if len(raw_sig) != 64:
        raise ProvenanceHold("invalid root signature size")
    payload = _strict_base64(envelope["payload"], max_size=8192)
    try:
        Ed25519PublicKey.from_public_bytes(root_key).verify(
            raw_sig, pae(POLICY_TYPE, payload),
        )
    except (ValueError, InvalidSignature) as exc:
        raise ProvenanceHold("root policy signature invalid") from exc
    return _json_strict(payload)


def verify_policy_and_image(
    *,
    policy_envelope: dict[str, Any],
    root_public_key: bytes,
    expected_root_sha256: str,
    builder_public_key: bytes,
    attestation_envelope: dict[str, Any],
    runtime_record: dict[str, Any],
    expected_service: str,
    expected_commit: str,
    expected_manifest_sha256: str,
    expected_builder_id: str,
    expected_build_type: str,
    externally_required_epoch: int,
    check_time_epoch: int,
    revoked_builder_key_sha256: frozenset[str],
) -> dict[str, Any]:
    """Cryptographically validate signer delegation but NEVER grant release."""
    if expected_service not in REPOS:
        raise ProvenanceHold("service outside allowlisted repository map")
    _hex(expected_commit, GIT_SHA, "source commit")
    _hex(expected_manifest_sha256, SHA256, "OCI digest")
    _hex(expected_builder_id, TYPE_URI, "builder ID")
    _hex(expected_build_type, TYPE_URI, "build type")
    if not isinstance(root_public_key, bytes) or len(root_public_key) != 32:
        raise ProvenanceHold("missing operator root")
    if not isinstance(builder_public_key, bytes) or len(builder_public_key) != 32:
        raise ProvenanceHold("missing builder signing key")
    if hmac.compare_digest(root_public_key, builder_public_key):
        raise ProvenanceHold("builder key and operator root may not be identical")
    if type(externally_required_epoch) is not int or not (
        1 <= externally_required_epoch <= MAX_POLICY_EPOCH
    ):
        raise ProvenanceHold("invalid independently required policy generation")
    if type(check_time_epoch) is not int or check_time_epoch < 1:
        raise ProvenanceHold("invalid policy evaluation clock")
    if not isinstance(revoked_builder_key_sha256, frozenset) or any(
        not isinstance(x, str) or SHA256.fullmatch(x) is None
        for x in revoked_builder_key_sha256
    ):
        raise ProvenanceHold("invalid independent revocation snapshot")
    fingerprint = hashlib.sha256(builder_public_key).hexdigest()
    if fingerprint in revoked_builder_key_sha256:
        raise ProvenanceHold("builder signing key explicitly revoked")
    policy = _signed_policy(
        policy_envelope, root_public_key, expected_root_sha256,
    )
    _exact(policy, FIELDS, "signed operator policy")
    if policy["schema"] != SCHEMA:
        raise ProvenanceHold("unsupported signed policy schema")
    if policy["epoch"] != externally_required_epoch:
        raise ProvenanceHold("signed epoch differs from independent witness")
    not_before = policy["not_before"]
    expires_at = policy["expires_at"]
    if (
        type(not_before) is not int or type(expires_at) is not int
        or not (0 < not_before < expires_at <= not_before + MAX_POLICY_WINDOW)
    ):
        raise ProvenanceHold("invalid signed authorization lifetime")
    if not not_before <= check_time_epoch < expires_at:
        raise ProvenanceHold("signed authorization expired or premature")
    required = {
        "schema": SCHEMA,
        "epoch": externally_required_epoch,
        "not_before": not_before,
        "expires_at": expires_at,
        "service": expected_service,
        "source_commit": expected_commit,
        "manifest_sha256": expected_manifest_sha256,
        "builder_id": expected_builder_id,
        "build_type": expected_build_type,
        "builder_key_sha256": fingerprint,
    }
    if policy != required:
        raise ProvenanceHold("signed builder policy scope mismatch")
    result = verify(
        envelope=attestation_envelope,
        public_key=builder_public_key,
        expected_key_sha256=fingerprint,
        expected_service=expected_service,
        expected_git_sha=expected_commit,
        expected_builder_id=expected_builder_id,
        expected_build_type=expected_build_type,
        expected_manifest_sha256=expected_manifest_sha256,
        runtime_record=runtime_record,
    )
    if (result.get("signature_valid_under_supplied_key") is not True
            or result.get("promotion_authorized") is not False):
        raise ProvenanceHold("underlying DSSE verifier trust state invalid")
    return {
        **result,
        "separate_builder_and_policy_keys": True,
        "operator_root_signature_under_supplied_key_valid": True,
        "builder_statement_signature_valid": True,
        "external_policy_epoch_matched": True,
        "policy_valid_at_supplied_time": True,
        "independent_operator_root_custody_proven": False,
        "non_restorable_epoch_witness_proven": False,
        "clock_authenticity_proven": False,
        "revocation_freshness_proven": False,
        "signed_source_to_runtime_release_authority": False,
        "production_decision": "HOLD",
        "promotion_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--policy", required=True)
    p.add_argument("--attestation", required=True)
    p.add_argument("--root-public-key", required=True)
    p.add_argument("--builder-public-key", required=True)
    p.add_argument("--root-sha256", required=True)
    p.add_argument("--service", choices=sorted(REPOS), required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--builder-id", required=True)
    p.add_argument("--build-type", required=True)
    p.add_argument("--required-epoch", type=int, required=True)
    p.add_argument("--revoked-builder-sha256", action="append", default=[])
    args = p.parse_args(argv)
    try:
        runtime = _runtime_record(args.service)
        policy = _json_strict(_read_public_file(args.policy, max_length=32000))
        statement = _json_strict(_read_public_file(args.attestation, max_length=256000))
        root = _read_public_file(args.root_public_key, max_length=32)
        builder = _read_public_file(args.builder_public_key, max_length=32)
        result = verify_policy_and_image(
            policy_envelope=policy,
            root_public_key=root,
            expected_root_sha256=args.root_sha256,
            builder_public_key=builder,
            attestation_envelope=statement,
            runtime_record=runtime,
            expected_service=args.service,
            expected_commit=args.commit,
            expected_manifest_sha256=args.manifest_sha256,
            expected_builder_id=args.builder_id,
            expected_build_type=args.build_type,
            externally_required_epoch=args.required_epoch,
            check_time_epoch=int(time.time()),
            revoked_builder_key_sha256=frozenset(args.revoked_builder_sha256),
        )
    except ProvenanceHold as exc:
        print(json.dumps({
            "production_decision": "HOLD", "promotion_authorized": False,
            "error": str(exc),
        }, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
