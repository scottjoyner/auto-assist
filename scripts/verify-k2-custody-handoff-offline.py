#!/usr/bin/env python3
"""K2 external-custody handoff CLAIMS: offline authentication, never admission.

No production trust roots are configured. A signed statement cannot prove
administrative independence or rollback protection. No network/files/graph IO.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SCHEMA = "assistx.k2-external-custody-claims.v1"
NODE = "x1-370"
MODEL_ID = "k2-horizon-0.9b"
MODEL_SHA256 = "371010db1807bb07b62e738422ee0de26c1e15a347f31108ed2c6e219095a8b8"
DOMAIN = b"assistx-k2-external-custody-claims-v1\0"
# Must be set by a separate human-authorized code review with actual
# independent evidence. These are intentionally NEVER taken from input.
APPROVED_OPERATOR_PUBLIC_KEY_SHA256 = ""
APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256 = ""
MAX_BYTES = 8192
MAX_AGE_MS = 86_400_000
MAX_WINDOW_MS = 86_400_000
SHA = re.compile(r"[0-9a-f]{64}\Z")
FIELDS = {
    "schema", "node", "model_id", "model_sha256",
    "challenge_hex", "issued_at_ms", "expires_at_ms",
    "custody_evidence", "attestations",
}
EVIDENCE_FIELDS = {
    "independent_administration_reference_sha256",
    "protected_key_provenance_sha256",
    "external_highwater_anchor_reference_sha256",
    "offhost_rollback_recovery_reference_sha256",
    "hardware_attestation_policy_reference_sha256",
}
ATTESTATIONS = {"operator", "custodian"}
ATTEST_FIELDS = {"public_key_b64", "signature_b64"}


class CustodyHold(ValueError):
    """Static non-sensitive refusal reason."""


def deny(message: str) -> None:
    raise CustodyHold(message)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    output: dict = {}
    for key, val in pairs:
        if key in output:
            deny("duplicate_json_key")
        output[key] = val
    return output


def canonical(value: dict) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise CustodyHold("unserializable_claims") from exc


def b64_exact(value: Any, size: int) -> bytes:
    if not isinstance(value, str) or len(value) > 128:
        deny("invalid_signature_encoding")
    try:
        result = base64.b64decode(value, validate=True)
    except (base64.binascii.Error, ValueError) as exc:
        raise CustodyHold("invalid_signature_encoding") from exc
    if len(result) != size or base64.b64encode(result).decode() != value:
        deny("noncanonical_signature_encoding")
    return result


def verify(raw: bytes, *, expected_challenge_hex: str, now_ms: int | None = None) -> dict:
    # A self-provided key inside an envelope is NEVER a trust root.
    if (not isinstance(APPROVED_OPERATOR_PUBLIC_KEY_SHA256, str)
            or not SHA.fullmatch(APPROVED_OPERATOR_PUBLIC_KEY_SHA256)
            or not isinstance(APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256, str)
            or not SHA.fullmatch(APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256)):
        deny("external_operator_and_custodian_pins_not_provisioned")
    if APPROVED_OPERATOR_PUBLIC_KEY_SHA256 == APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256:
        deny("operator_custodian_key_roles_not_separate")
    if not isinstance(expected_challenge_hex, str) or not SHA.fullmatch(
        expected_challenge_hex
    ):
        deny("missing_external_review_challenge")
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        deny("claims_document_size_invalid")
    try:
        doc = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
            parse_constant=lambda _: deny("nonfinite_json_number"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CustodyHold("claims_json_invalid") from exc
    if not isinstance(doc, dict) or set(doc) != FIELDS:
        deny("unreviewed_claims_fields")
    if raw != canonical(doc):
        deny("noncanonical_claims_bytes")
    if (doc["schema"] != SCHEMA or doc["node"] != NODE
            or doc["model_id"] != MODEL_ID or doc["model_sha256"] != MODEL_SHA256):
        deny("wrong_model_or_claim_domain")
    if doc["challenge_hex"] != expected_challenge_hex:
        deny("custody_review_challenge_mismatch")
    issued, expires = doc["issued_at_ms"], doc["expires_at_ms"]
    now = int(datetime.now(UTC).timestamp() * 1000) if now_ms is None else now_ms
    if (type(now) is not int or type(issued) is not int or type(expires) is not int
            or issued > now or now - issued > MAX_AGE_MS
            or expires <= now or expires <= issued
            or expires - issued > MAX_WINDOW_MS):
        deny("custody_claim_window_invalid")
    evidence = doc["custody_evidence"]
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_FIELDS:
        deny("custody_evidence_schema_invalid")
    if any(
        not isinstance(evidence[k], str)
        or not SHA.fullmatch(evidence[k])
        or evidence[k] == "0" * 64
        for k in EVIDENCE_FIELDS
    ):
        deny("custody_evidence_reference_invalid")
    attest = doc["attestations"]
    if not isinstance(attest, dict) or set(attest) != ATTESTATIONS:
        deny("missing_or_extra_attestation_role")
    unsigned = {k: v for k, v in doc.items() if k != "attestations"}
    hashes: dict[str, str] = {}
    for role, pin in (
        ("operator", APPROVED_OPERATOR_PUBLIC_KEY_SHA256),
        ("custodian", APPROVED_CUSTODIAN_PUBLIC_KEY_SHA256),
    ):
        info = attest[role]
        if not isinstance(info, dict) or set(info) != ATTEST_FIELDS:
            deny("attestation_fields_invalid")
        public = b64_exact(info["public_key_b64"], 32)
        digest = hashlib.sha256(public).hexdigest()
        if digest != pin:
            deny("attestation_key_not_preapproved")
        signature = b64_exact(info["signature_b64"], 64)
        try:
            Ed25519PublicKey.from_public_bytes(public).verify(
                signature, DOMAIN + role.encode() + b"\0" + canonical(unsigned)
            )
        except (InvalidSignature, ValueError) as exc:
            raise CustodyHold("custody_attestation_signature_invalid") from exc
        hashes[role] = digest
    # Even correct cryptography does NOT validate independent administration,
    # genuine attached evidence, key custody, or physical TPM quote.
    return {
        "kind": "k2-external-custody-claims-offline-review-v1",
        "operator_signature_valid": True,
        "custodian_signature_valid": True,
        "operator_key_sha256": hashes["operator"],
        "custodian_key_sha256": hashes["custodian"],
        "evidence_hash_claims_present": True,
        "evidence_contents_independently_verified": False,
        "independent_administration_verified": False,
        "protected_key_custody_verified": False,
        "external_antirollback_verified": False,
        "external_challenge_consumed_once": False,
        "hardware_attestation_verified": False,
        "canonical_operator_approval_verified": False,
        "can_dispatch": False,
        "decision": "HOLD",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-challenge", required=True,
        help="fresh external review challenge obtained outside the packet",
    )
    args = parser.parse_args()
    try:
        report = verify(
            sys.stdin.buffer.read(MAX_BYTES + 1),
            expected_challenge_hex=args.expected_challenge,
        )
        print(json.dumps(report, sort_keys=True))
    except CustodyHold as exc:
        print(json.dumps({
            "decision": "HOLD", "can_dispatch": False, "reason_code": str(exc),
        }, sort_keys=True))
        return 2
    # Valid claims remain non-admitting, exit code 2 even when authenticated.
    return 2


if __name__ == "__main__":
    sys.exit(main())
