"""Research-only image-pinned, separately signed corpus checkpoint check.

An image-pinned witness is NOT independently controlled, durable high-water
or rollback resistant when an operator can replace that image/checkpoint.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_CHECKPOINT_BYTES = 4096
MAX_GENERATION = (1 << 63) - 1
CHECKPOINT_ENV = "HERMES_REQUIRE_CORPUS_CHECKPOINT"
CHECKPOINT_PATH_ENV = "HERMES_CORPUS_CHECKPOINT_PATH"
WITNESS_KEY_ENV = "HERMES_CORPUS_WITNESS_KEY_PATH"


def reject(why: str) -> None:
    raise ValueError("Hermes checkpoint admission: " + why)


def _immutable_path(path: str, read_root: str, write_root: str) -> str:
    if (not isinstance(path, str) or not path.startswith("/")
            or path in ("/", read_root, write_root)
            or ".." in path.split("/") or "//" in path or path.endswith("/")
            or os.path.realpath(path) != path):
        reject("invalid image-pinned checkpoint or witness path")
    for root in (read_root, write_root):
        if os.path.commonpath((root, path)) == root:
            reject("witness/checkpoint overlaps corpus or scratch")
    cursor = Path("/")
    for piece in Path(path).parts[1:]:
        cursor = cursor / piece
        if cursor.is_symlink():
            reject("witness/checkpoint path has a symlink component")
    return path


def verify_checkpoint(*, read_root: str, write_root: str,
                      corpus_public_key: bytes, signed_manifest: bytes,
                      checkpoint_path: str, witness_key_path: str) -> int:
    """Verify distinct Ed25519 witness and exact image-pinned manifest bytes.

    Return generation only as descriptive evidence; no persistent high-water
    is advanced, and this must never itself authorize production execution.
    """
    from assistx.hermes_corpus_custody import _safe_regular, canonical
    cp = _immutable_path(checkpoint_path, read_root, write_root)
    wk = _immutable_path(witness_key_path, read_root, write_root)
    if cp == wk:
        reject("witness and checkpoint paths overlap")
    try:
        key = _safe_regular(wk, 32)
        raw = _safe_regular(cp, MAX_CHECKPOINT_BYTES)
        if len(key) != 32 or len(corpus_public_key) != 32 or key == corpus_public_key:
            reject("distinct 32-byte witness key required")
        payload = json.loads(raw)
        if (not isinstance(payload, dict)
                or set(payload) != {
                    "schema", "generation", "root", "manifest_sha256",
                    "corpus_key_sha256", "signature"}
                or type(payload["schema"]) is not int
                or payload["schema"] != 1
                or type(payload["generation"]) is not int
                or not 1 <= payload["generation"] <= MAX_GENERATION
                or payload["root"] != read_root
                or payload["manifest_sha256"] != hashlib.sha256(signed_manifest).hexdigest()
                or payload["corpus_key_sha256"] != hashlib.sha256(corpus_public_key).hexdigest()):
            reject("checkpoint digest, root, schema or generation mismatch")
        signature = payload["signature"]
        if not isinstance(signature, str) or len(signature) != 128:
            reject("invalid checkpoint signature length")
        Ed25519PublicKey.from_public_bytes(key).verify(
            bytes.fromhex(signature),
            canonical({k: v for k, v in payload.items() if k != "signature"}),
        )
        return payload["generation"]
    except (ValueError, OSError, TypeError, OverflowError,
            json.JSONDecodeError, InvalidSignature, UnicodeError) as err:
        # Do not leak raw file bytes, checkpoint content or secrets.
        reject("untrusted or unavailable independent checkpoint: "
               + type(err).__name__)
