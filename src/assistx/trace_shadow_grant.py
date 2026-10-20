"""Node-bound signed synthetic grants; deliberately NOT AssistX task authority.

Only grants for probe.noop.v1 are supported. These cannot promote a live
claim, schedule a fleet node, or authorize arbitrary commands.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .trace_execution_adapter import TraceDenied

SCHEMA = "assistx.synthetic-shadow-grant.v1"
ISSUER = "offline-shadow-test-fixture"
ALGORITHM = "Ed25519"
MAX_TTL_MS = 30_000
MAX_FUTURE_SKEW_MS = 2_000
HEXDIGEST = re.compile(r"^[0-9a-f]{64}$")
ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,160}$")
FIELDS = frozenset(
    {
        "schema",
        "issuer",
        "algorithm",
        "key_id",
        "grant_id",
        "node_id",
        "task_id",
        "claim_id",
        "command_id",
        "issued_at_ms",
        "expires_at_ms",
        "signature",
    }
)


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(encoded: str) -> bytes:
    if not isinstance(encoded, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", encoded):
        raise TraceDenied("invalid_grant_signature_format")
    try:
        return base64.urlsafe_b64decode(encoded + "==")
    except (ValueError, base64.binascii.Error) as exc:
        raise TraceDenied("invalid_grant_signature_format") from exc


def private_key(path: Path) -> Ed25519PrivateKey:
    raw = _read_regular(path, private=True)
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except (ValueError, TypeError) as exc:
        raise TraceDenied("invalid_private_grant_signer") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise TraceDenied("invalid_private_grant_signer")
    return key


def _read_regular(path: Path, *, private: bool) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise TraceDenied("grant_key_unavailable") from exc
    try:
        meta = os.fstat(fd)
        if (
            not stat.S_ISREG(meta.st_mode)
            or meta.st_nlink != 1
            or meta.st_uid != os.getuid()
            or meta.st_mode & 0o022
            or (private and meta.st_mode & 0o077)
        ):
            raise TraceDenied("unsafe_grant_key_permissions")
        if meta.st_size > 16 * 1024:
            raise TraceDenied("grant_key_too_large")
        return os.read(fd, meta.st_size + 1)
    finally:
        os.close(fd)


def public_key(path: Path) -> Ed25519PublicKey:
    raw = _read_regular(path, private=False)
    try:
        key = serialization.load_pem_public_key(raw)
    except (ValueError, TypeError) as exc:
        raise TraceDenied("invalid_grant_verification_key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise TraceDenied("invalid_grant_verification_key")
    return key


def new_grant(*, signer: Ed25519PrivateKey, node_id: str, now_ms: int | None = None) -> dict[str, Any]:
    if not isinstance(node_id, str) or not ID.fullmatch(node_id):
        raise TraceDenied("invalid_grant_target")
    stamp = int(time.time() * 1000) if now_ms is None else now_ms
    grant_id = uuid.uuid4().hex
    fields = {
        "schema": SCHEMA,
        "issuer": ISSUER,
        "algorithm": ALGORITHM,
        "key_id": "shadow-grant-" + node_id + "-v1",
        "grant_id": grant_id,
        "node_id": node_id,
        "task_id": "shadow-" + grant_id,
        "claim_id": "shadow-claim-" + grant_id,
        "command_id": "probe.noop.v1",
        "issued_at_ms": stamp,
        "expires_at_ms": stamp + MAX_TTL_MS,
    }
    return {**fields, "signature": _b64(signer.sign(canonical(fields)))}


def verify_grant(
    grant: dict[str, Any],
    *,
    node_id: str,
    verifier: Ed25519PublicKey,
    now_ms: int | None = None,
    revoked_grant_ids: frozenset[str] = frozenset(),
) -> str:
    if not isinstance(grant, dict) or set(grant) != FIELDS:
        raise TraceDenied("invalid_grant_schema")
    if grant["schema"] != SCHEMA or grant["issuer"] != ISSUER or grant["algorithm"] != ALGORITHM:
        raise TraceDenied("not_a_synthetic_shadow_grant")
    for name in ("grant_id", "node_id", "task_id", "claim_id", "key_id"):
        if not isinstance(grant[name], str) or not ID.fullmatch(grant[name]):
            raise TraceDenied("invalid_grant_field")
    if grant["node_id"] != node_id:
        raise TraceDenied("wrong_execution_node")
    if grant["key_id"] != "shadow-grant-" + node_id + "-v1":
        raise TraceDenied("wrong_grant_key_id")
    if grant["command_id"] != "probe.noop.v1":
        raise TraceDenied("unapproved_command_id")
    if grant["task_id"] != "shadow-" + grant["grant_id"] or grant["claim_id"] != "shadow-claim-" + grant["grant_id"]:
        raise TraceDenied("grant_lineage_invalid")
    if type(grant["issued_at_ms"]) is not int or type(grant["expires_at_ms"]) is not int:
        raise TraceDenied("grant_clock_invalid")
    issued, expires = grant["issued_at_ms"], grant["expires_at_ms"]
    if expires <= issued or expires - issued > MAX_TTL_MS:
        raise TraceDenied("grant_ttl_invalid")
    current = int(time.time() * 1000) if now_ms is None else now_ms
    if issued > current + MAX_FUTURE_SKEW_MS:
        raise TraceDenied("grant_issued_in_future")
    if expires <= current:
        raise TraceDenied("grant_expired")
    if grant["grant_id"] in revoked_grant_ids:
        raise TraceDenied("grant_revoked")
    unsigned = {k: v for k, v in grant.items() if k != "signature"}
    try:
        verifier.verify(_decode(grant["signature"]), canonical(unsigned))
    except InvalidSignature as exc:
        raise TraceDenied("grant_signature_invalid") from exc
    return hashlib.sha256(canonical(grant)).hexdigest()
