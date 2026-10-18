"""Offline DSSE Ed25519/SLSA-v1 subject-to-running-image verifier.

Intentionally narrow subset of SLSA provenance v1, not a generic SLSA
validator or an attestation *issuer*. Requires independent operator-pinned
expected builder, source commit, public-key SHA256 and build type. A valid
cryptographic signature does not prove the signer is a trusted builder.
Every result remains HOLD, with no production execution authority.

Only reads local files and invokes the companion five-service read-only
runtime custody audit. No Docker writes, remote registries, secret stores,
key creation, package installation or build/push/deploy operations.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PAYLOAD_TYPE = "application/vnd.in-toto+json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
TYPE_URI = re.compile(r"https://[A-Za-z0-9._/-]{3,240}\Z")
REPOS = {
    "assistx-api": "https://github.com/scottjoyner/auto-assist",
    "assistx-worker": "https://github.com/scottjoyner/auto-assist",
    "assistx-hermes-adapter": "https://github.com/scottjoyner/auto-assist",
    "git-router-1": "https://github.com/scottjoyner/auto-router",
}
MAX_FILE_BYTES = 256_000
MAX_PAYLOAD_BYTES = 128_000


class ProvenanceHold(ValueError):
    """Missing, malformed, untrusted, or mismatched provenance."""


def _pairs_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceHold("duplicate JSON key in signed statement")
        result[key] = value
    return result


def _json_strict(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_unique,
                           parse_constant=lambda _x: (_ for _ in ()).throw(
                               ProvenanceHold("nonstandard JSON number")
                           ))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ProvenanceHold("malformed signed JSON") from exc
    if not isinstance(value, dict):
        raise ProvenanceHold("attestation JSON must be an object")
    return value


def _read_public_file(path: str, *, max_length: int) -> bytes:
    """Prevent path aliases, symlink replacement and unbounded JSON input."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ProvenanceHold("absolute local attestation file required")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not (0 < info.st_size <= max_length):
                raise ProvenanceHold("attestation file is nonregular or too large")
            return stream.read(max_length + 1)
    except OSError as exc:
        raise ProvenanceHold("attestation file unavailable") from exc


def _strict_base64(encoded: Any, *, max_size: int) -> bytes:
    if not isinstance(encoded, str) or len(encoded) > 4 * ((max_size + 2) // 3):
        raise ProvenanceHold("encoded signature or payload length invalid")
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProvenanceHold("noncanonical base64 signature or payload") from exc
    if len(value) > max_size or base64.b64encode(value).decode("ascii") != encoded:
        raise ProvenanceHold("base64 data exceeds limit or is noncanonical")
    return value


def pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE v1 pre-auth encoding: domain-separate payload and content type."""
    kind = payload_type.encode("utf-8")
    return (b"DSSEv1 " + str(len(kind)).encode("ascii") + b" " + kind
            + b" " + str(len(payload)).encode("ascii") + b" " + payload)


def _exact(doc: Any, names: set[str], label: str) -> dict[str, Any]:
    if not isinstance(doc, dict) or set(doc) != names:
        raise ProvenanceHold(label + " missing or unknown attestation fields")
    return doc


def _input_text(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ProvenanceHold("invalid expected " + label)
    return value


def verify(
    *,
    envelope: dict[str, Any],
    public_key: bytes,
    expected_key_sha256: str,
    expected_service: str,
    expected_git_sha: str,
    expected_builder_id: str,
    expected_build_type: str,
    expected_manifest_sha256: str,
    runtime_record: dict[str, Any],
) -> dict[str, Any]:
    """Verify format/signature/equalities; intentionally deny release authority.

    Caller must establish that *external* pinned expectations are approved
    independently of the untrusted signer and current Docker daemon.
    """
    if expected_service not in REPOS:
        raise ProvenanceHold("service does not map to trusted source repository")
    _input_text(expected_git_sha, GIT_SHA, "Git commit")
    _input_text(expected_manifest_sha256, SHA256, "manifest SHA256")
    _input_text(expected_key_sha256, SHA256, "pinned key fingerprint")
    _input_text(expected_builder_id, TYPE_URI, "builder ID")
    _input_text(expected_build_type, TYPE_URI, "build type")
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise ProvenanceHold("Ed25519 trusted key must be exactly 32 bytes")
    fingerprint = hashlib.sha256(public_key).hexdigest()
    if not hmac.compare_digest(expected_key_sha256, fingerprint):
        raise ProvenanceHold("trusted key is not the independently pinned fingerprint")

    _exact(envelope, {"payloadType", "payload", "signatures"}, "DSSE envelope")
    if envelope["payloadType"] != PAYLOAD_TYPE:
        raise ProvenanceHold("wrong DSSE authenticated payload type")
    sigs = envelope["signatures"]
    if not isinstance(sigs, list) or len(sigs) != 1:
        raise ProvenanceHold("expected exactly one pinned Ed25519 DSSE signer")
    sig = _exact(sigs[0], {"keyid", "sig"}, "DSSE signature")
    # DSSE keyid is only a hint, never authority.
    if sig["keyid"] != fingerprint:
        raise ProvenanceHold("DSSE signer hint conflicts with pinned key")
    signature = _strict_base64(sig["sig"], max_size=64)
    if len(signature) != 64:
        raise ProvenanceHold("Ed25519 signature length invalid")
    payload = _strict_base64(envelope["payload"], max_size=MAX_PAYLOAD_BYTES)
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, pae(PAYLOAD_TYPE, payload),
        )
    except (ValueError, InvalidSignature) as exc:
        raise ProvenanceHold("DSSE signature does not match pinned Ed25519 key") from exc

    statement = _json_strict(payload)
    _exact(statement, {"_type", "subject", "predicateType", "predicate"},
           "in-toto statement")
    if statement["_type"] != STATEMENT_TYPE or statement["predicateType"] != PREDICATE_TYPE:
        raise ProvenanceHold("wrong in-toto Statement or SLSA predicate type")
    expected_subject = [{
        "name": "service:" + expected_service,
        "digest": {"sha256": expected_manifest_sha256},
    }]
    if statement["subject"] != expected_subject:
        raise ProvenanceHold("signed image subject does not equal expected service/digest")
    predicate = _exact(
        statement["predicate"], {"buildDefinition", "runDetails"},
        "SLSA predicate",
    )
    definition = _exact(
        predicate["buildDefinition"],
        {"buildType", "externalParameters", "resolvedDependencies"},
        "SLSA build definition",
    )
    if definition["buildType"] != expected_build_type:
        raise ProvenanceHold("signed build type mismatch")
    expected_parameters = {
        "sourceRepository": REPOS[expected_service],
        "sourceRevision": expected_git_sha,
        "service": expected_service,
    }
    if definition["externalParameters"] != expected_parameters:
        raise ProvenanceHold("signed build parameters mismatch")
    expected_dep = [{
        "uri": "git+" + REPOS[expected_service],
        "digest": {"gitCommit": expected_git_sha},
    }]
    if definition["resolvedDependencies"] != expected_dep:
        raise ProvenanceHold("signed resolved source dependency mismatch")
    details = _exact(predicate["runDetails"], {"builder"}, "SLSA run details")
    if details["builder"] != {"id": expected_builder_id}:
        raise ProvenanceHold("signed builder identity mismatch")

    if not isinstance(runtime_record, dict) or runtime_record.get("name") != expected_service:
        raise ProvenanceHold("runtime evidence is for another service")
    runtime_image = runtime_record.get("image")
    if not isinstance(runtime_image, dict) or not isinstance(
        runtime_image.get("manifest_digest_values"), list
    ):
        raise ProvenanceHold("missing local runtime image manifest evidence")
    # A manifest digest is *not* the Docker local image/config digest.
    manifests = runtime_image["manifest_digest_values"]
    if not manifests or any(
        not isinstance(m, str) or m != "sha256:" + expected_manifest_sha256
        for m in manifests
    ):
        raise ProvenanceHold("signed manifest subject not pinned to runtime image")
    image_id = runtime_image.get("local_image_id")
    container_id = runtime_record.get("container_id")
    if (not isinstance(image_id, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            or not isinstance(container_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", container_id)):
        raise ProvenanceHold("runtime image/container immutable identity missing")
    if runtime_record.get("state") != "running":
        raise ProvenanceHold("runtime service is not running")

    return {
        "service": expected_service,
        "source_repository": REPOS[expected_service],
        "expected_git_sha": expected_git_sha,
        "signed_manifest_digest": "sha256:" + expected_manifest_sha256,
        "runtime_local_image_id": image_id,
        "runtime_container_id": container_id,
        "signature_valid_under_supplied_key": True,
        "signed_subject_matches_observed_manifest": True,
        "externally_authorized_builder_root_proven": False,
        "signed_source_to_runtime_release_authority": False,
        "production_decision": "HOLD",
        "promotion_authorized": False,
        "limitations": (
            "This verifies a statement signed under the user-supplied pinned "
            "key and matching local Docker manifest observation. It does "
            "NOT establish independent signer custody, builder integrity, "
            "transparency inclusion, revocation, or execution authorization."
        ),
    }


def _runtime_record(service: str) -> dict[str, Any]:
    if service not in REPOS:
        raise ProvenanceHold("non-allowlisted source service")
    path = Path(__file__).resolve().with_name("read_only_image_custody.py")
    spec = importlib.util.spec_from_file_location("local_custody_prober", path)
    if spec is None or spec.loader is None:
        raise ProvenanceHold("companion local Docker auditor unavailable")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        result = module.observe()
    except (OSError, RuntimeError, ImportError, ValueError) as exc:
        raise ProvenanceHold("stable independent local image inventory unavailable") from exc
    if (result.get("production_decision") != "HOLD"
            or result.get("two_pass_identity_stable") is not True
            or result.get("signed_source_to_image_verified") is not False):
        raise ProvenanceHold("local evidence reported unexpected trust state")
    matches = [x for x in result.get("containers", []) if x.get("name") == service]
    if len(matches) != 1:
        raise ProvenanceHold("target runtime evidence missing or ambiguous")
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--envelope", required=True, help="local DSSE JSON file")
    p.add_argument("--public-key", required=True, help="local raw 32-byte Ed25519 public key")
    p.add_argument("--key-sha256", required=True, help="operator-pinned public-key SHA256")
    p.add_argument("--service", choices=sorted(REPOS), required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--builder-id", required=True)
    p.add_argument("--build-type", required=True)
    p.add_argument("--manifest-sha256", required=True)
    args = p.parse_args(argv)
    try:
        # No artifact subject verification can be released without a full
        # stable current local-Docker read-only check.
        runtime = _runtime_record(args.service)
        envelope = _json_strict(_read_public_file(args.envelope, max_length=MAX_FILE_BYTES))
        pub = _read_public_file(args.public_key, max_length=32)
        result = verify(
            envelope=envelope, public_key=pub,
            expected_key_sha256=args.key_sha256,
            expected_service=args.service, expected_git_sha=args.commit,
            expected_builder_id=args.builder_id,
            expected_build_type=args.build_type,
            expected_manifest_sha256=args.manifest_sha256,
            runtime_record=runtime,
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except ProvenanceHold as exc:
        print(json.dumps({
            "production_decision": "HOLD",
            "promotion_authorized": False,
            "signed_source_to_runtime_release_authority": False,
            "error": str(exc),
        }, sort_keys=True))
        return 3


if __name__ == "__main__":
    sys.exit(main())
