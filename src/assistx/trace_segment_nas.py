"""Fail-closed NAS publisher for *staged* encrypted trace segments.

No worker integration, no journal mutation, no deletion of archive history.
A separate local signed custody ledger survives NAS disappearance but is NOT
an independent WORM witness. Mount identity is rechecked before every write.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .trace_execution_adapter import TraceDenied, TraceReceiptStore
from .trace_segment_bundle import _filename, _private, _read, verify_bundle
from .trace_segment_plan import canonical, digest

SCHEMA = "assistx.trace-nas-publish.v1"
MAX_CIPHER_BYTES = 8 * 1024 * 1024 + 100_000
NODE_IDS = frozenset({"xwing", "scotts-macbook-air"})


def assert_mount(root: Path, *, expected_source: str, expected_target: str) -> None:
    """Require the configured real CIFS mount; autofs or rootfs is not sufficient."""
    if not root.is_dir() or root.is_symlink():
        raise TraceDenied("nas_root_missing_or_symlink")
    if not expected_source.startswith("//") or not expected_target.startswith("/"):
        raise TraceDenied("nas_expected_identity_invalid")
    try:
        result = subprocess.run(
            ["findmnt", "-rn", "-T", str(root), "-o", "FSTYPE,SOURCE,TARGET"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TraceDenied("nas_mount_probe_failed") from exc
    if result.returncode or not result.stdout.strip():
        raise TraceDenied("nas_mount_probe_failed")
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    expected = ["cifs", expected_source, expected_target]
    automount = ["autofs", "systemd-1", expected_target]
    # Linux systemd automounts can return BOTH rows for -T. An autofs
    # wrapper alone is never enough; exactly one matching CIFS is required.
    if rows.count(expected) != 1 or any(row not in (expected, automount) for row in rows):
        raise TraceDenied("nas_mount_identity_mismatch")
    # Protect from a symlinked destination outside the expected mount.
    base = Path(expected_target).resolve(strict=True)
    resolved = root.resolve(strict=True)
    if not resolved.is_relative_to(base) or resolved == base:
        raise TraceDenied("nas_destination_outside_mount")


def _witness_rows(path: Path, key: bytes) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _read(path, 8 * 1024 * 1024)
    if payload and not payload.endswith(b"\n"):
        raise TraceDenied("nas_witness_torn")
    rows = []
    previous = ""
    for line in payload.splitlines():
        try:
            signed = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise TraceDenied("nas_witness_invalid") from exc
        signature = signed.get("signature")
        unsigned = {k: v for k, v in signed.items() if k != "signature"}
        if (
            unsigned.get("schema") != SCHEMA
            or unsigned.get("previous_signature") != previous
            or not isinstance(signature, str)
            or not hmac.compare_digest(signature, hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest())
        ):
            raise TraceDenied("nas_witness_invalid")
        previous = signature
        rows.append(signed)
    return rows


def _safe_file_target(parent: Path, name: str) -> Path:
    if not re.fullmatch(r"(?:index\.json|segment-[0-9]{6}-[a-f0-9]{20}\.jsonl\.gpg)", name):
        raise TraceDenied("nas_archive_name_invalid")
    return parent / name


def _put_exact(source: Path, target: Path, *, ceiling: int, mount_check: Any) -> bool:
    """No-overwrite publish; valid prior objects can be resumed, never replaced."""
    expected = _read(source, ceiling)
    mount_check()
    if target.exists() or target.is_symlink():
        if digest(_read(target, ceiling)) != digest(expected):
            raise TraceDenied("nas_existing_segment_conflict")
        return False
    # Copy to a unique sibling, fsync, publish with link(O_EXCL semantics);
    # rename/replace could overwrite a concurrent writer and is not used.
    with tempfile.TemporaryDirectory(prefix=".nas-staging-", dir=target.parent) as work:
        staging = Path(work) / "payload"
        fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=True) as file:
                file.write(expected)
                file.flush()
                os.fsync(file.fileno())
        except Exception:
            raise
        if digest(_read(staging, ceiling)) != digest(expected):
            raise TraceDenied("nas_staged_hash_mismatch")
        mount_check()
        try:
            os.link(staging, target, follow_symlinks=False)
        except FileExistsError:
            if digest(_read(target, ceiling)) != digest(expected):
                raise TraceDenied("nas_concurrent_target_mismatch") from None
            return False
        except OSError as exc:
            raise TraceDenied("nas_atomic_publish_unavailable") from exc
        handle = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
    return True


def _index_for_bundle(root: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        bundle = json.loads(_read(root / "index.json", 8 * 1024 * 1024))
        entries = bundle["entries"]
        plan = bundle["plan"]
        if (
            not isinstance(entries, list)
            or not isinstance(plan["segments"], list)
            or len(entries) != len(plan["segments"])
        ):
            raise ValueError("invalid")
        names = []
        for i, (meta, entry) in enumerate(zip(plan["segments"], entries)):  # noqa: B905 -- macOS Python 3.9
            expected = _filename(i + 1, meta["sha256"])
            if entry["file"] != expected:
                raise ValueError("invalid")
            names.append(expected)
        return bundle, names
    except (KeyError, TypeError, ValueError) as exc:
        raise TraceDenied("nas_staged_bundle_index_invalid") from exc


def publish_bundle(
    *,
    source: Path,
    destination: Path,
    witness_root: Path,
    expected_mount_source: str,
    expected_mount_target: str,
    node_id: str,
    passphrase: Path,
    signing_key: bytes,
) -> dict[str, Any]:
    """Verify source and post-publish restore before append-only local witness.

    destination is a pre-existing directory UNDER an explicitly expected mount.
    This code does not create destination or touch the original journal.
    """
    if node_id not in NODE_IDS or not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise TraceDenied("nas_publisher_identity_invalid")
    _private(source)
    _private(witness_root)
    expected_base = Path(expected_mount_target).resolve(strict=True)
    if (
        source.resolve().is_relative_to(expected_base)
        or witness_root.resolve().is_relative_to(expected_base)
        or source.resolve() == witness_root.resolve()
    ):
        raise TraceDenied("nas_source_or_witness_not_local")
    assert_mount(destination, expected_source=expected_mount_source, expected_target=expected_mount_target)
    try:
        _private(destination)
    except TraceDenied as exc:
        raise TraceDenied("nas_destination_permissions_unsafe") from exc
    raw = verify_bundle(source, node_id=node_id, passphrase=passphrase, signing_key=signing_key)
    bundle, names = _index_for_bundle(source)
    journal_hash = digest(raw)
    # Separate node/content addressing preserves all distinct generations.
    node_root = destination / node_id
    content_root = node_root / journal_hash
    lockfd = os.open(witness_root / ".publish.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        lockstat = os.fstat(lockfd)
        if (
            not stat.S_ISREG(lockstat.st_mode)
            or lockstat.st_uid != os.getuid()
            or lockstat.st_nlink != 1
            or lockstat.st_mode & 0o077
        ):
            raise TraceDenied("nas_publish_lock_unsafe")
        fcntl.flock(lockfd, fcntl.LOCK_EX)
        witness_path = witness_root / "published.jsonl"
        rows = _witness_rows(witness_path, signing_key)
        node_root = destination / node_id
        # Never initialize a fresh custody history over an already complete
        # remote archive after the separate signed witness disappears.
        if not witness_path.exists() and node_root.is_dir():
            if any(node_root.glob("*/index.json")):
                raise TraceDenied("nas_witness_missing_with_archives_present")
        duplicates = [r for r in rows if r.get("node_id") == node_id and r.get("journal_sha256") == journal_hash]
        if len(duplicates) > 1:
            raise TraceDenied("nas_duplicate_witness")
        for path in (node_root, content_root):
            assert_mount(destination, expected_source=expected_mount_source, expected_target=expected_mount_target)
            if path.is_symlink():
                raise TraceDenied("nas_destination_symlink")
            if not path.exists():
                path.mkdir(mode=0o700)
            if not path.is_dir():
                raise TraceDenied("nas_destination_invalid")
            _private(path)

        def mount_check() -> None:
            assert_mount(destination, expected_source=expected_mount_source, expected_target=expected_mount_target)

        for name in names:
            _put_exact(
                source / name, _safe_file_target(content_root, name), ceiling=MAX_CIPHER_BYTES, mount_check=mount_check
            )
        # Publish the index last, so an interrupted upload has no complete
        # manifest. Re-entering resumes only byte-identical segments.
        _put_exact(source / "index.json", content_root / "index.json", ceiling=8 * 1024 * 1024, mount_check=mount_check)
        mount_check()
        restored = verify_bundle(content_root, node_id=node_id, passphrase=passphrase, signing_key=signing_key)
        if digest(restored) != journal_hash or restored != raw:
            raise TraceDenied("nas_full_restore_mismatch")
        witness_meta = {
            "schema": SCHEMA,
            "node_id": node_id,
            "journal_sha256": journal_hash,
            "records": bundle["plan"]["records"],
            "segment_count": len(names),
            "ciphertext_index_sha256": digest(_read(content_root / "index.json", 8 * 1024 * 1024)),
            "mount_source": expected_mount_source,
            "mount_target": expected_mount_target,
            "relative_archive": node_id + "/" + journal_hash,
        }
        if duplicates:
            prior = {k: v for k, v in duplicates[0].items() if k not in ("signature", "previous_signature")}
            if prior != witness_meta:
                raise TraceDenied("nas_existing_witness_conflict")
            return {
                "ok": True,
                "reused": True,
                "segments": len(names),
                "journal_sha256": journal_hash,
                "witness_signed": True,
            }
        signed = {**witness_meta, "previous_signature": rows[-1]["signature"] if rows else ""}
        signed["signature"] = hmac.new(signing_key, canonical(signed), hashlib.sha256).hexdigest()
        fd = os.open(witness_root / "published.jsonl", os.O_APPEND | os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            stat_result = os.fstat(fd)
            if (
                not stat.S_ISREG(stat_result.st_mode)
                or stat_result.st_mode & 0o077
                or stat_result.st_uid != os.getuid()
                or stat_result.st_nlink != 1
            ):
                raise TraceDenied("nas_witness_file_unsafe")
            data = canonical(signed) + b"\n"
            if os.write(fd, data) != len(data):
                raise TraceDenied("nas_witness_short_write")
            os.fsync(fd)
        finally:
            os.close(fd)
        dfd = os.open(witness_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        return {
            "ok": True,
            "reused": False,
            "segments": len(names),
            "journal_sha256": journal_hash,
            "witness_signed": True,
        }
    finally:
        os.close(lockfd)


def verify_published_bundle(
    *,
    destination: Path,
    witness_root: Path,
    expected_mount_source: str,
    expected_mount_target: str,
    node_id: str,
    journal_sha256: str,
    passphrase: Path,
    signing_key: bytes,
) -> dict[str, Any]:
    """Independent replay from a separate locally signed witness and NAS data.

    This is a second-process verifier, NOT a remote or WORM witness.  It does
    not modify archives, journal data or the local witness chain.
    """
    if node_id not in NODE_IDS or not re.fullmatch(r"[a-f0-9]{64}", journal_sha256):
        raise TraceDenied("nas_verify_identity_invalid")
    if not isinstance(signing_key, bytes) or len(signing_key) < 32:
        raise TraceDenied("nas_verify_key_invalid")
    _private(witness_root)
    if witness_root.resolve().is_relative_to(Path(expected_mount_target).resolve(strict=True)):
        raise TraceDenied("nas_witness_not_local")
    assert_mount(destination, expected_source=expected_mount_source, expected_target=expected_mount_target)
    try:
        _private(destination)
    except TraceDenied as exc:
        raise TraceDenied("nas_destination_permissions_unsafe") from exc
    rows = _witness_rows(witness_root / "published.jsonl", signing_key)
    candidates = [r for r in rows if r.get("node_id") == node_id and r.get("journal_sha256") == journal_sha256]
    if len(candidates) != 1:
        raise TraceDenied("nas_signed_custody_record_missing_or_duplicate")
    witness = candidates[0]
    if (
        witness.get("mount_source") != expected_mount_source
        or witness.get("mount_target") != expected_mount_target
        or witness.get("relative_archive") != f"{node_id}/{journal_sha256}"
    ):
        raise TraceDenied("nas_witness_mount_or_path_changed")
    archive = destination / node_id / journal_sha256
    if archive.is_symlink() or archive.parent.is_symlink():
        raise TraceDenied("nas_verify_archive_symlink")
    _private(archive)
    if digest(_read(archive / "index.json", 8 * 1024 * 1024)) != witness.get("ciphertext_index_sha256"):
        raise TraceDenied("nas_signed_index_hash_changed")
    restored = verify_bundle(archive, node_id=node_id, passphrase=passphrase, signing_key=signing_key)
    records = TraceReceiptStore._verify_data(restored)
    if (
        digest(restored) != journal_sha256
        or len(records) != witness.get("records")
        or len(list(archive.glob("segment-*.jsonl.gpg"))) != witness.get("segment_count")
    ):
        raise TraceDenied("nas_witness_restore_does_not_match")
    assert_mount(destination, expected_source=expected_mount_source, expected_target=expected_mount_target)
    return {
        "ok": True,
        "node_id": node_id,
        "journal_sha256": journal_sha256,
        "records": len(records),
        "segments": witness["segment_count"],
        "local_witness_verified": True,
        "nas_restore_verified": True,
        "independent_worm_witness": False,
    }
