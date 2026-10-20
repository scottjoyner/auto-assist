"""Offline encrypted segment staging, idempotent local custody only.

Not an NAS exporter: no remote mount writes, no journal mutation, no
production authority. A separately authenticated NAS publisher and
independent durable witness remain mandatory.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .trace_execution_adapter import TraceDenied
from .trace_segment_plan import canonical, digest, plan_segments, verify_segments

SCHEMA = "assistx.encrypted-trace-segments.v1"


def _private(path: Path) -> None:
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise TraceDenied("unsafe_segment_directory")


def _read(path: Path, max_bytes: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise TraceDenied("segment_file_unavailable") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1 or st.st_mode & 0o077:
            raise TraceDenied("segment_file_unsafe")
        if st.st_size > max_bytes:
            raise TraceDenied("segment_file_too_large")
        return os.read(fd, st.st_size + 1)
    finally:
        os.close(fd)


def _check_passphrase(path: Path) -> None:
    raw = _read(path, 4096)
    if len(raw.strip()) < 32:
        raise TraceDenied("segment_weak_passphrase")


def _gpg(raw: bytes | None, path: Path, passphrase: Path, *, decrypt: bool) -> bytes:
    cmd = [
        "gpg",
        "--batch",
        "--quiet",
        "--pinentry-mode",
        "loopback",
        "--passphrase-file",
        str(passphrase),
    ]
    if decrypt:
        cmd += ["--decrypt", str(path)]
    else:
        cmd += ["--cipher-algo", "AES256", "--symmetric", "--output", str(path)]
    try:
        result = subprocess.run(cmd, input=raw, capture_output=True, check=False, timeout=35)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TraceDenied("segment_crypto_process_failed") from exc
    if result.returncode:
        raise TraceDenied("segment_crypto_failed")
    if decrypt and len(result.stdout) > 8 * 1024 * 1024:
        raise TraceDenied("segment_decrypted_too_large")
    return result.stdout


def _filename(i: int, plain_sha256: str) -> str:
    return f"segment-{i:06d}-{plain_sha256[:20]}.jsonl.gpg"


def _lock(root: Path) -> int:
    fd = os.open(root / ".segment-lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
        os.close(fd)
        raise TraceDenied("segment_lock_unsafe")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _manifest_bytes(bundle: dict[str, Any]) -> bytes:
    return canonical(bundle) + b"\n"


def verify_bundle(root: Path, *, node_id: str, passphrase: Path, signing_key: bytes) -> bytes:
    """Independently decrypt and validate every exact segment against signed index."""
    _private(root)
    _check_passphrase(passphrase)
    index = _read(root / "index.json", 8 * 1024 * 1024)
    try:
        bundle = json.loads(index)
    except (ValueError, TypeError) as exc:
        raise TraceDenied("segment_index_invalid") from exc
    if not isinstance(bundle, dict) or set(bundle) != {"schema", "plan", "entries", "signature"}:
        raise TraceDenied("segment_index_schema_invalid")
    unsigned = {k: v for k, v in bundle.items() if k != "signature"}
    expected = hmac.new(signing_key, canonical(unsigned), hashlib.sha256).hexdigest()
    if not isinstance(bundle["signature"], str) or not hmac.compare_digest(expected, bundle["signature"]):
        raise TraceDenied("segment_index_signature_invalid")
    if bundle["schema"] != SCHEMA or not isinstance(bundle["entries"], list):
        raise TraceDenied("segment_index_schema_invalid")
    plan = bundle["plan"]
    if not isinstance(plan, dict) or plan.get("node_id") != node_id:
        raise TraceDenied("segment_index_node_mismatch")
    if len(bundle["entries"]) != len(plan["segments"]):
        raise TraceDenied("segment_index_count_mismatch")
    pieces = []
    for i, (meta, record) in enumerate(zip(plan["segments"], bundle["entries"])):  # noqa: B905 -- Python 3.9
        name = _filename(i + 1, meta["sha256"])
        if record.get("file") != name:
            raise TraceDenied("segment_index_filename_invalid")
        ciphertext_path = root / name
        ciphertext = _read(ciphertext_path, 8 * 1024 * 1024 + 100_000)
        if digest(ciphertext) != record.get("ciphertext_sha256"):
            raise TraceDenied("segment_ciphertext_hash_invalid")
        plain = _gpg(None, ciphertext_path, passphrase, decrypt=True)
        if digest(plain) != meta["sha256"]:
            raise TraceDenied("segment_plaintext_hash_invalid")
        pieces.append(plain)
    return verify_segments(plan, pieces, node_id=node_id, signing_key=signing_key)


def stage_bundle(
    raw: bytes,
    *,
    root: Path,
    node_id: str,
    passphrase: Path,
    signing_key: bytes,
    max_segment_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    """Idempotent, exclusive-locked local staging. Never touches the source bytes."""
    _private(root)
    _check_passphrase(passphrase)
    plan, chunks = plan_segments(
        raw,
        node_id=node_id,
        signing_key=signing_key,
        max_segment_bytes=max_segment_bytes,
    )
    fd = _lock(root)
    try:
        if (root / "index.json").exists():
            restored = verify_bundle(root, node_id=node_id, passphrase=passphrase, signing_key=signing_key)
            if digest(restored) != digest(raw):
                raise TraceDenied("segment_existing_bundle_conflict")
            return {"ok": True, "reused": True, "segments": len(chunks), "sha256": digest(raw)}
        entries = []
        for i, (meta, part) in enumerate(zip(plan["segments"], chunks)):  # noqa: B905 -- Python 3.9
            name = _filename(i + 1, meta["sha256"])
            dest = root / name
            if dest.exists():
                _read(dest, 8 * 1024 * 1024 + 100_000)
                if _gpg(None, dest, passphrase, decrypt=True) != part:
                    raise TraceDenied("segment_partial_resume_mismatch")
            else:
                with tempfile.TemporaryDirectory(prefix=".encrypt-", dir=root) as temp:
                    temporary = Path(temp) / "encrypted.gpg"
                    _gpg(part, temporary, passphrase, decrypt=False)
                    if _gpg(None, temporary, passphrase, decrypt=True) != part:
                        raise TraceDenied("segment_local_roundtrip_invalid")
                    os.chmod(temporary, 0o600)
                    os.link(temporary, dest, follow_symlinks=False)
                    with dest.open("rb") as handle:
                        os.fsync(handle.fileno())
                    dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(dfd)
                    finally:
                        os.close(dfd)
            entries.append({"file": name, "ciphertext_sha256": digest(_read(dest, 8 * 1024 * 1024 + 100_000))})
        unsigned = {"schema": SCHEMA, "plan": plan, "entries": entries}
        bundle = {
            **unsigned,
            "signature": hmac.new(signing_key, canonical(unsigned), hashlib.sha256).hexdigest(),
        }
        with tempfile.TemporaryDirectory(prefix=".index-", dir=root) as temp:
            temporary = Path(temp) / "index.json"
            temporary.write_bytes(_manifest_bytes(bundle))
            temporary.chmod(0o600)
            os.link(temporary, root / "index.json", follow_symlinks=False)
            dfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        if verify_bundle(root, node_id=node_id, passphrase=passphrase, signing_key=signing_key) != raw:
            raise TraceDenied("segment_bundle_roundtrip_invalid")
        return {"ok": True, "reused": False, "segments": len(chunks), "sha256": digest(raw)}
    finally:
        os.close(fd)
