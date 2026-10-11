"""Optional independent Ed25519 witness for in-process AssistX projections.

A verified signature authenticates projection bytes only, not physical model
execution, sidecar identity, signer custody, or approved weight hashes.
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .runtime_projection import projection_checksum
from .runtime_projection_v2 import signing_message

_PINNED_HEX = re.compile(r"^[0-9a-f]{64}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]{86}$")


def verify_pinned_projection_signature(document: Any, pinned_hex: str) -> bool:
    """Independently verify exact canonical issuance with a configured public key.

    Never loads private keys or returns public keys, signature strings, model
    aliases or hashes. No network/file operations or signing side effects.
    """
    if not isinstance(document, dict):
        return False
    if not isinstance(pinned_hex, str) or not _PINNED_HEX.fullmatch(pinned_hex):
        return False
    signature = document.get("signature")
    if not isinstance(signature, str) or not _B64URL.fullmatch(signature):
        return False
    if (document.get("signature_algorithm") != "Ed25519"
        or document.get("schema_version") != "2"
        or document.get("source") != "assistx"):
        return False
    try:
        if projection_checksum(document) != document.get("checksum"):
            return False
        raw = base64.b64decode(signature+"==", altchars=b"-_", validate=True)
        if len(raw) != 64:
            return False
        signer = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pinned_hex))
        signer.verify(raw, signing_message(document))
        return True
    except (ValueError, TypeError, binascii.Error, InvalidSignature, OverflowError):
        return False
