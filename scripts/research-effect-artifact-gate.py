#!/usr/bin/env python3
"""Disposable filesystem effect artifact gate before local witness issuance.

This is NOT independently owned physical resource execution. It only asks
whether a matching fsynced file (distinct from source and anchor SQL rows)
is present at signing time. The same OS administrator can forge the file.
Import-only, deny-only research; no production routing, network or keys.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
from functools import cache
from pathlib import Path
from typing import Any

_RESOURCE = "synthetic-resource-effect-v1.bin"
_MARKER = ".synthetic-crash-lab-only"
_MAX_BYTES = 65_536


class ArtifactChangedAfterWitnessCommit(Exception):
    """Anchor/witness may be committed; never automatically retry."""


@cache
def _issuer() -> Any:
    p = Path(__file__).with_name("research-durable-witness-issuance.py")
    spec = importlib.util.spec_from_file_location("effect_artifact_research_issuer", p)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic_witness_issuer_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_path(path: Path) -> bool:
    if not isinstance(path, Path) or path.name != _RESOURCE or path.is_symlink():
        return False
    try:
        return (path.parent / _MARKER).read_text() == "DISPOSABLE_ONLY\n"
    except (OSError, UnicodeError):
        return False


def create_disposable_effect(path: Path, payload: bytes) -> str:
    """Actually write and fsync one synthetic artifact; never overwrite."""
    if (
        not _valid_path(path) or path.exists()
        or type(payload) is not bytes or not 1 <= len(payload) <= _MAX_BYTES
    ):
        raise ValueError("synthetic_effect_creation_denied")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as writer:
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        os.close(fd)
    return hashlib.sha256(payload).hexdigest()


def _read_pinned(fd: int, path: Path, expected_sha: str) -> bool:
    """Check same opened inode and current path; identity is NOT custody."""
    try:
        info = os.fstat(fd)
        linked = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size == 0 or info.st_size > _MAX_BYTES
            or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            return False
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        seen = 0
        while True:
            data = os.read(fd, 16_384)
            if not data:
                break
            seen += len(data)
            if seen > _MAX_BYTES:
                return False
            digest.update(data)
        if seen != info.st_size or digest.hexdigest() != expected_sha:
            return False
        last = os.fstat(fd)
        path_after = path.stat(follow_symlinks=False)
        return (
            (last.st_dev, last.st_ino, last.st_size, last.st_mtime_ns)
            == (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            and (path_after.st_dev, path_after.st_ino)
            == (info.st_dev, info.st_ino)
        )
    except OSError:
        return False


def issue_with_effect(
    member_path: Path, *, witness_id: str, effect_path: Path,
    pinned_effect_sha256: str, tip: Any, expected: Any,
    resource: Path, anchor: Path, signer: Any,
    key_epoch: int, challenge: str, now_ms: int,
) -> tuple[bool, str, dict[str, Any] | None]:
    """Gate #393 synthetic issuance on a fsynced, pinned artifact readback.

    The issuer signs the existing receipt tip, NOT an independent effect
    authority. Path/hash/checkpoint all remain controlled by this test host.
    """
    if (
        not _valid_path(effect_path)
        or not isinstance(pinned_effect_sha256, str)
        or len(pinned_effect_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in pinned_effect_sha256)
    ):
        return False, "EFFECT_ARTIFACT_CUSTODY_OR_DIGEST_MISSING", None
    try:
        fd = os.open(effect_path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False, "EFFECT_ARTIFACT_ABSENT_OR_UNAVAILABLE", None
    try:
        if not _read_pinned(fd, effect_path, pinned_effect_sha256):
            return False, "EFFECT_ARTIFACT_IDENTITY_OR_CONTENT_MISMATCH", None

        def checkpoint(stage: str) -> None:
            if stage == "before_witness_commit":
                if not _read_pinned(fd, effect_path, pinned_effect_sha256):
                    raise ValueError("synthetic_effect_changed_before_commit")
            elif stage == "after_witness_commit_before_ack":
                if not _read_pinned(fd, effect_path, pinned_effect_sha256):
                    raise ArtifactChangedAfterWitnessCommit(
                        "EFFECT_CHANGED_AFTER_COMMIT_OUTCOME_UNCERTAIN_NO_RETRY"
                    )

        try:
            result = _issuer().issue(
                member_path, witness_id=witness_id, tip=tip, expected=expected,
                resource=resource, anchor=anchor, signer=signer,
                key_epoch=key_epoch, challenge=challenge, now_ms=now_ms,
                callback=checkpoint,
            )
        except _issuer().UncertainWitnessCommit as exc:
            # #393 wraps any post-COMMIT callback failure in typed uncertainty.
            raise ArtifactChangedAfterWitnessCommit(
                "WITNESS_COMMIT_OR_EFFECT_UNCERTAIN_NO_RETRY"
            ) from exc
        except ValueError:
            return False, "EFFECT_CHANGED_BEFORE_COMMIT", None

        return result
    finally:
        os.close(fd)


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_EXTERNAL_EFFECT_OR_RELEASE_ENTRYPOINT")
