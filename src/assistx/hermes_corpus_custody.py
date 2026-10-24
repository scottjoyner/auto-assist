"""Opt-in signed subset of the Hermes read-only knowledge corpus.

This is a bounded *startup* integrity check using an image-pinned public key.
It does not establish signer independence, rollback-resistant freshness, or
protection against a privileged host mutating files after verification.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MANIFEST_NAME = ".hermes-corpus-manifest.json"
SIGNED_ENV = "HERMES_REQUIRE_SIGNED_CORPUS"
KEY_ENV = "HERMES_CORPUS_TRUST_KEY_PATH"
MAX_MANIFEST_BYTES = 262144
MAX_ENTRIES = 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
LABEL = re.compile(r"[A-Za-z0-9_.-]+\Z")


def fail(detail: str) -> None:
    raise ValueError("Hermes signed corpus admission: " + detail)


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _confined(root: str, path: str) -> bool:
    return os.path.commonpath((root, path)) == root


def _safe_regular(path: str, max_bytes: int) -> bytes:
    """Never open a symlink leaf; require the exact bounded regular file."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes or st.st_size < 0:
            fail("file is not a bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            buf = stream.read(max_bytes + 1)
        if len(buf) > max_bytes:
            fail("input changed during bounded read")
        return buf
    finally:
        if fd >= 0:
            os.close(fd)


def _safe_relpath(root: str, rel: str) -> str:
    if (not isinstance(rel, str) or len(rel) > 256 or rel.startswith("/")
        or rel in ("", ".", "..")
        or any(p in ("", ".", "..") or not LABEL.fullmatch(p)
               for p in rel.split("/"))
        or len(rel.split("/")) > 2
        or not rel.endswith((".md", ".txt"))):
        fail("unapproved corpus file name")
    result = os.path.join(root, rel)
    if not _confined(root, result):
        fail("corpus file outside read root")
    # Reject symlinked parent directory as well as symlinked file.
    current = Path(root)
    for component in rel.split("/"):
        current = current / component
        if current.is_symlink():
            fail("symlink inside signed corpus")
    return result


def verify_signed_corpus(read_root: str, write_root: str,
                         trust_key_path: str) -> dict[str, str]:
    """Verify a signed, exact allowlist of bounded synthetic/approved text.

    The public verifier key is supplied by the image/operator, NOT located in
    a mutable corpus or scratch volume. External key custody is still required.
    """
    root = os.path.realpath(read_root)
    output = os.path.realpath(write_root)
    key = os.path.realpath(trust_key_path)
    if (not trust_key_path or not os.path.isabs(trust_key_path)
            or key != trust_key_path or key in ("/", root, output)
            or _confined(root, key) or _confined(output, key)):
        fail("verifier key not outside mutable input/output mounts")
    if Path(trust_key_path).is_symlink():
        fail("symlink verifier key")
    manifest = os.path.join(root, MANIFEST_NAME)
    if Path(manifest).is_symlink():
        fail("symlink corpus manifest")
    try:
        trusted = _safe_regular(key, 32)
        if len(trusted) != 32:
            fail("Ed25519 trust root must be exactly 32 bytes")
        public = Ed25519PublicKey.from_public_bytes(trusted)
        raw = _safe_regular(manifest, MAX_MANIFEST_BYTES)
        document = json.loads(raw)
        if (not isinstance(document, dict)
                or set(document) != {"schema", "root", "entries", "signature"}
                or document.get("schema") != 1
                or document.get("root") != read_root
                or not isinstance(document.get("entries"), list)
                or not 1 <= len(document["entries"]) <= MAX_ENTRIES):
            fail("invalid signed corpus manifest envelope")
        sig = document["signature"]
        if not isinstance(sig, str) or len(sig) != 128:
            fail("invalid Ed25519 signature length")
        payload = {k: v for k, v in document.items() if k != "signature"}
        public.verify(bytes.fromhex(sig), canonical(payload))
        watch = os.getenv("HERMES_REQUIRE_CORPUS_CHECKPOINT", "")
        if watch not in ("", "false", "true"):
            fail("invalid witness checkpoint mode")
        if watch == "true":
            from assistx.hermes_corpus_checkpoint import verify_checkpoint
            verify_checkpoint(
                read_root=read_root, write_root=write_root,
                corpus_public_key=trusted, signed_manifest=raw,
                checkpoint_path=os.getenv("HERMES_CORPUS_CHECKPOINT_PATH", ""),
                witness_key_path=os.getenv("HERMES_CORPUS_WITNESS_KEY_PATH", ""),
            )
        expected: dict[str, str] = {}
        accumulated = 0
        for item in payload["entries"]:
            if (not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}
                    or not isinstance(item["size"], int)
                    or isinstance(item["size"], bool)
                    or not 0 <= item["size"] <= MAX_FILE_BYTES
                    or not isinstance(item["sha256"], str)
                    or not DIGEST.fullmatch(item["sha256"])):
                fail("invalid signed file entry")
            path = item["path"]
            full = _safe_relpath(root, path)
            if path in expected:
                fail("duplicate signed corpus entry")
            accumulated += item["size"]
            if accumulated > MAX_TOTAL_BYTES:
                fail("total corpus size exceeds bounded startup admission")
            data = _safe_regular(full, MAX_FILE_BYTES)
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                fail("corpus file differs from signed manifest")
            expected[path] = item["sha256"]
        # No unlisted context-bearing MD/TXT inputs, even if RO. Directory
        # enumeration is limited to the exact top level and one child depth.
        for location in os.scandir(root):
            if location.is_symlink():
                fail("symlink entry in corpus root")
            if location.is_file(follow_symlinks=False):
                if location.name.endswith((".md", ".txt")) and location.name not in expected:
                    fail("unsigned text input in corpus")
            elif location.is_dir(follow_symlinks=False):
                for child in os.scandir(location.path):
                    if child.is_symlink():
                        fail("symlink in corpus subtree")
                    rel = location.name + "/" + child.name
                    if child.is_dir(follow_symlinks=False):
                        fail("unbounded nested corpus directory")
                    if child.is_file(follow_symlinks=False) and child.name.endswith((".md", ".txt")) and rel not in expected:
                        fail("unsigned nested input")
        return expected
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError,
            InvalidSignature, TypeError, OverflowError) as exc:
        fail("untrusted or unreadable signed corpus: " + type(exc).__name__)


def verified_context_texts(read_root: str, write_root: str,
                           trust_key_path: str) -> list[tuple[str, str]]:
    """Return text bytes only after re-verification at consumption time.

    First verify manifest/signature/full allowlist, then read each consumed
    file *again through a single O_NOFOLLOW FD* and confirm its SHA-256.
    Never return a partial or unchecked context on a failed later file.
    This guards reads, not privileged host races after bytes were captured.
    """
    expected = verify_signed_corpus(read_root, write_root, trust_key_path)
    root = os.path.realpath(read_root)
    result: list[tuple[str, str]] = []
    for rel, digest in expected.items():
        full = _safe_relpath(root, rel)
        raw = _safe_regular(full, MAX_FILE_BYTES)
        if hashlib.sha256(raw).hexdigest() != digest:
            fail("signed input changed during consumption")
        result.append((rel, raw.decode("utf-8", errors="ignore")))
    return result
