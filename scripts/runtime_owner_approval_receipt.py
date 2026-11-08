"""Independent owner authorization for canonical runtime-projection grants.

No signing keys, auto-enrollment, CLI-selected trust roots or self-approval.
Missing root-owned trust anchor blocks ALL actual grant attempts.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

TRUST_PATH = Path("/etc/assistx/trust/runtime-approval-owner.pub")
SCHEMA = "assistx.canonical-runtime-owner-approval.v1"
KEY_ID = "assistx-runtime-approval-owner-v1"
SCOPE = "auto-assist:canonical-runtime-grant"
DOMAIN = b"auto-assist:canonical-runtime-owner-approval:v1\n"
MAX_RECEIPT = 8192
MAX_LIFETIME_MS = 300_000
FIELDS = frozenset(
    {
        "schema_version",
        "scope",
        "signature_algorithm",
        "signature_key_id",
        "approved_by",
        "approval_id",
        "manifest_checksum",
        "required_roster_sha256",
        "generation",
        "expected_current_generation",
        "issued_at_ms",
        "expires_at_ms",
        "nonce",
        "signature",
    }
)


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def signing_message(receipt: dict[str, Any]) -> bytes:
    return DOMAIN + _canonical({k: v for k, v in receipt.items() if k != "signature"})


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_owner_approval_field")
        result[key] = value
    return result


def read_receipt(path: Path) -> dict[str, Any]:
    """Read a capped detached receipt, refusing symlinks and special files."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("owner_approval_receipt_not_regular")
        raw = os.read(fd, MAX_RECEIPT + 1)
    finally:
        os.close(fd)
    if not raw or len(raw) > MAX_RECEIPT:
        raise ValueError("owner_approval_receipt_size_invalid")
    try:
        result = json.loads(
            raw,
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("owner_approval_receipt_json_invalid") from exc
    if not isinstance(result, dict):
        raise ValueError("owner_approval_receipt_not_object")
    return result


def load_root_owned_key() -> Ed25519PublicKey:
    """Require independently provisioned root-owned public key and safe path.

    There is deliberately NO caller-supplied path override. Public key can be
    world readable but must not be replaceable by the unprivileged app user.
    """
    for path in reversed((TRUST_PATH, *TRUST_PATH.parents)):
        state = path.lstat()
        if stat.S_ISLNK(state.st_mode) or state.st_uid != 0 or state.st_mode & 0o022:
            raise ValueError("owner_approval_trust_path_untrusted")
        if path == TRUST_PATH:
            if not stat.S_ISREG(state.st_mode) or not 40 <= state.st_size <= 4096:
                raise ValueError("owner_approval_trust_key_invalid")
        elif not stat.S_ISDIR(state.st_mode):
            raise ValueError("owner_approval_trust_directory_invalid")
    fd = os.open(TRUST_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        state = os.fstat(fd)
        if state.st_uid != 0 or not stat.S_ISREG(state.st_mode) or state.st_mode & 0o022:
            raise ValueError("owner_approval_trust_key_untrusted")
        raw = os.read(fd, 4097)
    finally:
        os.close(fd)
    if len(raw) > 4096:
        raise ValueError("owner_approval_trust_key_invalid")
    try:
        key = serialization.load_pem_public_key(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("owner_approval_trust_key_invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("owner_approval_trust_key_must_be_ed25519")
    return key


def verify_receipt(
    manifest: Any, receipt: dict[str, Any], key: Ed25519PublicKey, *, required_roster_sha256: str, now_ms: int
) -> str:
    """Verify signed exact manifest and operator identity; return receipt hash.

    Does NOT attest runtime evidence, change Task admission or grant a slot.
    """
    if not isinstance(receipt, dict) or set(receipt) != FIELDS:
        raise ValueError("owner_approval_receipt_fields_invalid")
    if (
        receipt["schema_version"] != SCHEMA
        or receipt["scope"] != SCOPE
        or receipt["signature_algorithm"] != "Ed25519"
        or receipt["signature_key_id"] != KEY_ID
    ):
        raise ValueError("owner_approval_receipt_domain_invalid")
    for field in ("approved_by", "approval_id"):
        value = receipt[field]
        if (
            type(value) is not str
            or not 1 <= len(value) <= 128
            or value.strip().lower() in ("unknown", "unresolved", "replace-me", "none", "null", "")
        ):
            raise ValueError("owner_approval_identity_invalid")
        if value != manifest.payload[field]:
            raise ValueError("owner_approval_identity_mismatch")
    if (
        type(receipt["manifest_checksum"]) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", receipt["manifest_checksum"])
        or receipt["manifest_checksum"] != manifest.checksum
    ):
        raise ValueError("owner_approval_manifest_mismatch")
    if (
        type(required_roster_sha256) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", required_roster_sha256)
        or type(receipt["required_roster_sha256"]) is not str
        or receipt["required_roster_sha256"] != required_roster_sha256
    ):
        raise ValueError("owner_approval_roster_checksum_mismatch")
    for field in ("generation", "expected_current_generation", "issued_at_ms", "expires_at_ms"):
        if type(receipt[field]) is not int:
            raise ValueError("owner_approval_integer_claim_invalid")
    for field in ("generation", "expected_current_generation"):
        if receipt[field] != manifest.payload[field]:
            raise ValueError("owner_approval_generation_mismatch")
    issued, expires = receipt["issued_at_ms"], receipt["expires_at_ms"]
    if (
        issued <= 0
        or expires <= issued
        or expires - issued > MAX_LIFETIME_MS
        or now_ms < issued - 15_000
        or now_ms >= expires
        or now_ms - issued > MAX_LIFETIME_MS
    ):
        raise ValueError("owner_approval_receipt_stale")
    if type(receipt["nonce"]) is not str or not re.fullmatch(r"[0-9a-f]{32}", receipt["nonce"]):
        raise ValueError("owner_approval_nonce_invalid")
    sig = receipt["signature"]
    if type(sig) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{86}", sig):
        raise ValueError("owner_approval_signature_malformed")
    try:
        decoded = base64.b64decode(sig + "==", altchars=b"-_", validate=True)
        if len(decoded) != 64 or (base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != sig):
            raise ValueError("owner_approval_signature_malformed")
        key.verify(decoded, signing_message(receipt))
    except (InvalidSignature, binascii.Error) as exc:
        raise ValueError("owner_approval_signature_invalid") from exc
    return hashlib.sha256(_canonical(receipt)).hexdigest()
