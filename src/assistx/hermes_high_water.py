"""Research opt-in challenge-response gate for a separately administered witness.

AF_UNIX is a transport, NOT an assertion of independent custody.
A configured witness must keep durable monotonic high-water state outside
the Hermes host's rollback/credential domain before production use.
Never accept unsigned, replayed or cached positive replies.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SOCKET_ENV = "HERMES_HIGH_WATER_SOCKET"
KEY_ENV = "HERMES_HIGH_WATER_TRUST_KEY_PATH"
OWNER_ENV = "HERMES_HIGH_WATER_OWNER"
MODE_ENV = "HERMES_REQUIRE_EXTERNAL_HIGH_WATER"
MAX_RECEIPT = 4096
TIMEOUT_SECONDS = 1.5
OWNER = re.compile(r"[A-Za-z0-9_.:@-]{1,128}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def deny(why: str) -> None:
    raise ValueError("Hermes high-water admission: " + why)


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def path_outside_mutable(path: str, read_root: str, write_root: str) -> str:
    if (not isinstance(path, str) or not path.startswith("/")
            or path.endswith("/") or "//" in path or ".." in path.split("/")
            or os.path.realpath(path) != path):
        deny("noncanonical or mutable witness path")
    for root in (read_root, write_root):
        if os.path.commonpath((root, path)) == root:
            deny("witness resides in mutable input or scratch")
    node = Path("/")
    for component in Path(path).parts[1:]:
        node = node / component
        if node.is_symlink():
            deny("symlinked witness path")
    return path


def verify_receipt(raw: bytes, *, public_key: bytes,
                   request: dict) -> None:
    """Verify fresh response + exact expected generation and hashes.

    A newer witness high-water forces the worker to stop and refresh its
    immutable image/checkpoint, rather than silently running an old epoch.
    """
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RECEIPT:
        deny("missing or oversized witness receipt")
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        deny("missing 32-byte pinned witness trust key")
    try:
        doc = json.loads(raw)
        required = {"schema", "nonce", "owner", "checkpoint_sha256",
                    "manifest_sha256", "generation",
                    "high_water_generation", "revoked", "signature"}
        if not isinstance(doc, dict) or set(doc) != required:
            deny("unexpected witness receipt fields")
        if type(doc["schema"]) is not int or doc["schema"] != 1:
            deny("bad witness schema")
        if (type(doc["generation"]) is not int
            or type(doc["high_water_generation"]) is not int
            or doc["generation"] <= 0 or doc["high_water_generation"] <= 0):
            deny("invalid generation")
        for field in ("nonce", "owner", "checkpoint_sha256",
                      "manifest_sha256", "generation"):
            if doc[field] != request[field]:
                deny("nonce, owner, generation or digest mismatch")
        if (type(doc["revoked"]) is not bool or doc["revoked"]
                or doc["high_water_generation"] != request["generation"]):
            deny("revoked, stale or divergent checkpoint")
        signature = doc["signature"]
        if (not isinstance(signature, str) or len(signature) != 128
                or not re.fullmatch("[0-9a-f]{128}", signature)):
            deny("invalid witness signature syntax")
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            bytes.fromhex(signature),
            canonical({k: v for k, v in doc.items() if k != "signature"}),
        )
    except (TypeError, ValueError, InvalidSignature, UnicodeError, OverflowError) as exc:
        deny("untrusted fresh witness receipt: " + type(exc).__name__)


def query_live_witness(*, read_root: str, write_root: str,
                       corpus_public_key: bytes, checkpoint_key: bytes,
                       checkpoint_raw: bytes, manifest_raw: bytes,
                       generation: int) -> None:
    """One bounded, fresh request. No offline/cache fallback, no retries.

    The separate witness must maintain and sign state outside this process.
    A same-host test listener only validates protocol plumbing, not custody.
    """
    from assistx.hermes_corpus_custody import _safe_regular
    sock_path = path_outside_mutable(
        os.getenv(SOCKET_ENV, ""), read_root, write_root)
    trust_path = path_outside_mutable(
        os.getenv(KEY_ENV, ""), read_root, write_root)
    owner = os.getenv(OWNER_ENV, "")
    if not OWNER.fullmatch(owner):
        deny("unapproved owner identity")
    try:
        public = _safe_regular(trust_path, 32)
        if (len(public) != 32 or public in (corpus_public_key, checkpoint_key)):
            deny("external authority must have distinct signing key")
        request = {
            "schema": 1, "nonce": secrets.token_hex(32), "owner": owner,
            "generation": generation,
            "checkpoint_sha256": hashlib.sha256(checkpoint_raw).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        }
        wire = canonical(request) + b"\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(TIMEOUT_SECONDS)
            channel.connect(sock_path)
            channel.sendall(wire)
            pieces = []
            remaining = MAX_RECEIPT + 2
            while remaining > 0:
                part = channel.recv(min(remaining, 1024))
                if not part:
                    break
                pieces.append(part)
                remaining -= len(part)
                if b"\n" in part:
                    break
            data = b"".join(pieces)
            if (not data.endswith(b"\n") or data.count(b"\n") != 1
                    or len(data) - 1 > MAX_RECEIPT):
                deny("missing or oversized bounded witness response")
        verify_receipt(data[:-1], public_key=public, request=request)
    except (OSError, TimeoutError, ValueError, TypeError) as exc:
        deny("external high-water unavailable or denied: " + type(exc).__name__)
