"""UNWIRED synthetic ASGI adapter for an authenticated observation-only gateway.

This module grants no HTTP route, role, model access, or fallback. A future
operator-owned gateway must authenticate the Tailnet client BEFORE signing.
The receiver must separately pin the signer and durable nonce store.
"""
from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .trusted_gateway_identity_contract import (
    GatewayIdentityDenied, verify_gateway_assertion,
)

_ALLOWED_RAW_PATH = b"/api/v1/runtime/observations"
_ALLOWED_METHOD = "GET"
_REQUIRED_SCOPE = "model:observe"
_CLAIM_HEADER = b"x-assistx-gateway-claim"
_SIGNATURE_HEADER = b"x-assistx-gateway-signature"
_B64URL = re.compile(rb"[A-Za-z0-9_-]+")


def _deny() -> None:
    raise GatewayIdentityDenied("signed observation identity not verified")


def _decode_base64url(value: bytes, *, max_decoded: int) -> bytes:
    if (type(value) is not bytes or not 1 <= len(value) <= max_decoded * 2
            or not _B64URL.fullmatch(value)):
        _deny()
    try:
        raw = base64.urlsafe_b64decode(value + b"=" * (-len(value) % 4))
    except (ValueError, binascii.Error):
        _deny()
    if (not raw or len(raw) > max_decoded
            or base64.urlsafe_b64encode(raw).rstrip(b"=") != value):
        _deny()
    return raw


def verify_observation_asgi_identity(
    *,
    scope: Mapping[str, Any],
    headers: Sequence[tuple[bytes, bytes]],
    receiver_public_key: bytes,
    receiver_key_id: str,
    now_ms: int,
    consume_nonce_once: Callable[[str, str, int], bool],
) -> str:
    """Authenticate a single GET observation request without header fallback.

    The caller must NOT use `scope["path"]` (it may be normalized) or a
    client-controlled forwarded URI. The signed target is the ASGI raw path;
    no query string is permitted in this deliberately narrow research slice.
    """
    if (not isinstance(scope, Mapping)
            or scope.get("type") != "http"
            or scope.get("method") != _ALLOWED_METHOD
            or scope.get("raw_path") != _ALLOWED_RAW_PATH
            or scope.get("query_string") != b""
            or not isinstance(headers, (tuple, list))
            or len(headers) > 96):
        _deny()
    found: dict[bytes, bytes] = {}
    for item in headers:
        if (not isinstance(item, (tuple, list)) or len(item) != 2
                or not isinstance(item[0], bytes)
                or not isinstance(item[1], bytes)):
            _deny()
        key = item[0].lower()
        if key in (_CLAIM_HEADER, _SIGNATURE_HEADER):
            if key in found:
                _deny()
            found[key] = item[1]
    if set(found) != {_CLAIM_HEADER, _SIGNATURE_HEADER}:
        _deny()
    claim = _decode_base64url(found[_CLAIM_HEADER], max_decoded=2048)
    signature = _decode_base64url(found[_SIGNATURE_HEADER], max_decoded=64)
    return verify_gateway_assertion(
        claim_bytes=claim,
        signature=signature,
        receiver_public_key=receiver_public_key,
        receiver_key_id=receiver_key_id,
        now_ms=now_ms,
        request_method=_ALLOWED_METHOD,
        request_target=_ALLOWED_RAW_PATH.decode("ascii"),
        required_scope=_REQUIRED_SCOPE,
        consume_nonce_once=consume_nonce_once,
    )
