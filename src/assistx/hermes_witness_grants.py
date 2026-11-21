"""Ed25519 owner-specific mutation grants for the research Hermes witness.

Signing grants must be issued by an independently administered authorizer.
Verifying a grant does NOT authenticate the transport, protect the SQLite
file from rollback, or grant rights to the research ledger's raw APIs.
"""
from __future__ import annotations

import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from assistx.hermes_high_water import canonical, OWNER, DIGEST

MAX_BYTES = 4096
MAX_GEN = (1 << 63) - 1
EMPTY = "0" * 64
_FIELDS = {
    "schema", "action", "owner", "nonce", "prior_generation",
    "prior_checkpoint_sha256", "prior_manifest_sha256",
    "generation", "checkpoint_sha256", "manifest_sha256",
    "signature",
}

class InvalidGrant(ValueError):
    pass


def need(value, why):
    if not value:
        raise InvalidGrant("Hermes owner-grant authorization: " + why)


def verify_owner_grant(raw: bytes, authorizer_public_key: bytes,
                       witness_signing_public_key: bytes | None = None) -> dict:
    """Return verified exact grant; never carry a signing private key."""
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES,
         "grant size or encoding invalid")
    need(type(authorizer_public_key) is bytes and len(authorizer_public_key) == 32,
         "missing pinned authorizer key")
    if witness_signing_public_key is not None:
        need(type(witness_signing_public_key) is bytes
             and len(witness_signing_public_key) == 32
             and witness_signing_public_key != authorizer_public_key,
             "authorizer and witness signer must be different")
    try:
        grant = json.loads(raw)
        need(type(grant) is dict and set(grant) == _FIELDS,
             "unknown or missing grant fields")
        need(type(grant["schema"]) is int and grant["schema"] == 1,
             "unrecognized grant schema")
        need(grant["action"] in ("publish", "revoke"),
             "unrecognized state mutation")
        need(type(grant["owner"]) is str
             and bool(OWNER.fullmatch(grant["owner"])),
             "untrusted owner")
        need(type(grant["nonce"]) is str
             and bool(re.fullmatch("[0-9a-f]{64}", grant["nonce"])),
             "untrusted grant nonce")
        need(all(type(grant[k]) is int and 0 <= grant[k] <= MAX_GEN
                 for k in ("generation", "prior_generation")),
             "invalid generation")
        need(grant["generation"] > 0, "missing target generation")
        need(all(type(grant[k]) is str and bool(DIGEST.fullmatch(grant[k]))
                 for k in ("checkpoint_sha256", "manifest_sha256",
                           "prior_checkpoint_sha256", "prior_manifest_sha256")),
             "malformed exact state digest")
        if grant["prior_generation"] == 0:
            need(grant["prior_checkpoint_sha256"] == EMPTY
                 and grant["prior_manifest_sha256"] == EMPTY,
                 "initial predecessor must be empty")
        need(type(grant["signature"]) is str
             and bool(re.fullmatch("[0-9a-f]{128}", grant["signature"])),
             "invalid Ed25519 grant signature")
        payload={k:v for k,v in grant.items() if k!="signature"}
        Ed25519PublicKey.from_public_bytes(authorizer_public_key).verify(
            bytes.fromhex(grant["signature"]), canonical(payload))
        return payload
    except (TypeError, ValueError, UnicodeError, OverflowError,
            InvalidSignature) as exc:
        raise InvalidGrant("Hermes owner-grant authorization: invalid or forged grant ("
                           + type(exc).__name__ + ")") from exc
