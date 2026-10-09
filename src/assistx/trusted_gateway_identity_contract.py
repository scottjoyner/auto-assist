"""Unwired deny-only contract for externally authenticated Tailnet gateway identity.

RESEARCH ONLY. This is NOT imported into FastAPI or accepted from any HTTP
header. The gateway must authenticate the Tailscale Serve identity *before*
signing. The backend must pin the public key in an owner-controlled trust store
and atomically consume nonces in independently protected durable shared state.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VERSION = "assistx.tailnet.gateway.identity.v1"
ISSUER = "assistx-verified-tailnet-gateway"
AUDIENCE = "assistx-backend"
PREFIX = b"assistx:gateway:identity:v1\x00"
MAX_TTL_MS = 30_000
FUTURE_SKEW_MS = 3_000
FIELDS = frozenset({
    "version", "issuer", "audience", "key_id", "subject",
    "issued_at_ms", "expires_at_ms", "nonce", "method", "target", "scope",
})
SUBJECT = re.compile(r"^[A-Za-z0-9._%+\-]{1,128}@[A-Za-z0-9.\-]{1,126}$")
KEY_ID = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
NONCE = re.compile(r"^[0-9a-f]{32}$")
SCOPE = re.compile(r"^[a-z][a-z0-9:._/-]{0,63}$")


class GatewayIdentityDenied(RuntimeError):
    """An assertion cannot establish authenticated receiver-owned identity."""


def _deny() -> None:
    raise GatewayIdentityDenied("gateway identity not independently verified")


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _deny()
        result[key] = value
    return result


def canonical_claim(claim: dict[str, object]) -> bytes:
    """No signer/provisioner is provided here; canonicalization only."""
    if type(claim) is not dict or set(claim) != FIELDS:
        _deny()
    try:
        encoded = json.dumps(
            claim, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _deny()
    if not encoded or len(encoded) > 2048:
        _deny()
    return encoded


def verify_gateway_assertion(
    *,
    claim_bytes: bytes,
    signature: bytes,
    receiver_public_key: bytes,
    receiver_key_id: str,
    now_ms: int,
    request_method: str,
    request_target: str,
    required_scope: str,
    consume_nonce_once: Callable[[str, str, int], bool],
) -> str:
    """Return the signed operator only after auth, binding, and replay custody.

    `consume_nonce_once` MUST be receiver-owned atomic state shared across all
    serving workers. It MUST fail closed on Redis/graph/store outage, failover
    or duplicate nonce, and protect its own generation custody. This library
    cannot authenticate or provision that store; it never grants a fallback.
    """
    if (type(claim_bytes) is not bytes or not 1 <= len(claim_bytes) <= 2048
            or type(signature) is not bytes or len(signature) != 64
            or type(receiver_public_key) is not bytes
            or len(receiver_public_key) != 32
            or type(receiver_key_id) is not str
            or not KEY_ID.fullmatch(receiver_key_id)
            or type(now_ms) is not int or now_ms <= 0
            or type(request_method) is not str
            or request_method not in ("GET", "POST")
            or type(request_target) is not str
            or not request_target.startswith("/")
            or not 1 <= len(request_target) <= 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in request_target)
            or "#" in request_target
            or type(required_scope) is not str
            or not SCOPE.fullmatch(required_scope)
            or not callable(consume_nonce_once)):
        _deny()
    try:
        claim = json.loads(
            claim_bytes.decode("ascii"),
            object_pairs_hook=_no_duplicates,
            parse_constant=lambda x: _deny(),
        )
    except (ValueError, TypeError, UnicodeError, GatewayIdentityDenied):
        _deny()
    if type(claim) is not dict or canonical_claim(claim) != claim_bytes:
        _deny()
    subject = claim["subject"]
    issued = claim["issued_at_ms"]
    expires = claim["expires_at_ms"]
    nonce = claim["nonce"]
    if (claim["version"] != VERSION or claim["issuer"] != ISSUER
            or claim["audience"] != AUDIENCE
            or claim["key_id"] != receiver_key_id
            or type(subject) is not str or not SUBJECT.fullmatch(subject)
            or type(nonce) is not str or not NONCE.fullmatch(nonce)
            or claim["method"] != request_method
            or claim["target"] != request_target
            or claim["scope"] != required_scope
            or type(issued) is not int or type(expires) is not int
            or issued > now_ms + FUTURE_SKEW_MS
            or expires <= now_ms or expires <= issued
            or expires - issued > MAX_TTL_MS):
        _deny()
    try:
        public = Ed25519PublicKey.from_public_bytes(receiver_public_key)
        public.verify(signature, PREFIX + claim_bytes)
    except (InvalidSignature, ValueError, TypeError):
        _deny()
    try:
        accepted = consume_nonce_once(subject, nonce, expires)
    except Exception:
        _deny()
    if accepted is not True:
        _deny()
    return subject
