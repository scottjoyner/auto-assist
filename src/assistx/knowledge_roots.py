"""Opt-in, fail-closed separation of Hermes knowledge sources and artifacts.

Both HERMES_KNOWLEDGE_READ_ROOT and HERMES_KNOWLEDGE_WRITE_ROOT must be
supplied together. Without them legacy KNOWLEDGE_ROOT behavior is unchanged.
These checks complement, but do NOT replace, Docker mount confinement.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Iterator

READ_ENV = "HERMES_KNOWLEDGE_READ_ROOT"
WRITE_ENV = "HERMES_KNOWLEDGE_WRITE_ROOT"
# Opt-in strict startup contract for restricted container builds.
MOUNT_GUARD_ENV = "HERMES_REQUIRE_MOUNT_ISOLATION"
WRITE_MOUNT_ENV = "HERMES_KNOWLEDGE_WRITE_MOUNT"


def _strict_path(raw: str, name: str) -> str:
    if (not raw or len(raw) > 4096 or not os.path.isabs(raw)
            or "\x00" in raw or os.path.normpath(raw) != raw):
        raise ValueError(f"{name} must be a normalized absolute path")
    # Reject existing symlinks in every component, not only the leaf.
    current = Path("/")
    for segment in Path(raw).parts[1:]:
        current = current / segment
        if current.is_symlink():
            raise ValueError(f"{name} resolves through a symbolic link")
    return os.path.realpath(raw)


def _mount_records(raw: str) -> dict[str, tuple[int, bool]]:
    """Parse kernel mountinfo only; reject malformed/ambiguous mount evidence.

    Each exact mountpoint maps to (mount-id, is-readonly). This is a
    containment check, NOT independent provenance of its backing source.
    """
    records: dict[str, tuple[int, bool]] = {}
    if not raw or len(raw) > 1_000_000:
        raise ValueError("missing or unbounded kernel mount evidence")
    for line in raw.splitlines():
        before, sep, after = line.partition(" - ")
        left = before.split()
        right = after.split()
        if not sep or len(left) < 6 or len(right) < 3:
            raise ValueError("malformed kernel mountinfo")
        mount_id, mountpoint, opts = left[0], left[4], left[5]
        if (not mount_id.isdecimal() or not mountpoint.startswith("/")
                or "\\" in mountpoint or not opts
                or not any(x in opts.split(",") for x in ("ro", "rw"))):
            raise ValueError("ambiguous kernel mountinfo record")
        if mountpoint in records:
            raise ValueError("duplicate visible mountpoint")
        flags = opts.split(",")
        if ("ro" in flags) == ("rw" in flags):
            raise ValueError("ambiguous mount access")
        records[mountpoint] = (int(mount_id), "ro" in flags)
    return records


def verify_mount_admission(
    read_root: str, write_root: str, *, write_mount: str,
    evidence: str | None = None,
) -> None:
    """Require exact RO source bind and distinct RW workspace mount.

    Enabled only by HERMES_REQUIRE_MOUNT_ISOLATION=true. Cannot attest
    the host-side source, its owner, or a separate administrative signer.
    """
    source = _strict_path(read_root, READ_ENV)
    output = _strict_path(write_root, WRITE_ENV)
    mount = _strict_path(write_mount, WRITE_MOUNT_ENV)
    if (source in ("/", "/root", "/home", "/media", "/mnt")
            or mount in ("/", "/root", "/home", "/media", "/mnt")
            or source == mount or _within(source, mount)
            or _within(mount, source) or not _within(mount, output)
            or mount == output):
        raise ValueError("knowledge mount boundaries overlap or are unsafe")
    if not os.path.isdir(source) or not os.path.isdir(mount):
        raise ValueError("required corpus or scratch mount missing")
    if evidence is None:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as fh:
            evidence = fh.read(1_000_001)
    records = _mount_records(evidence)
    ro = records.get(source)
    rw = records.get(mount)
    if ro is None or rw is None or not ro[1] or rw[1] or ro[0] == rw[0]:
        raise ValueError("kernel does not prove separate RO/RW mount boundaries")
    # Any nested mount under the source can undermine its RO guarantee.
    if any(_within(source, name) and name != source for name in records):
        raise ValueError("nested knowledge input mount not approved")
    # Likewise no unreviewed nested scratch mounts, even if read-only.
    if any(_within(mount, name) and name != mount for name in records):
        raise ValueError("nested scratch mount not approved")


def _within(parent: str, child: str) -> bool:
    return os.path.commonpath((parent, child)) == parent


def resolve_knowledge_roots(legacy: str, eval_path: str | None = None) -> tuple[str, str, bool]:
    read = os.getenv(READ_ENV, "")
    write = os.getenv(WRITE_ENV, "")
    if bool(read) != bool(write):
        raise ValueError("both Hermes knowledge split roots are required")
    if not read:
        if os.getenv(MOUNT_GUARD_ENV, "") not in ("", "false"):
            raise ValueError("mount isolation requires explicit split roots")
        if os.getenv("HERMES_REQUIRE_SIGNED_CORPUS", "") not in ("", "false"):
            raise ValueError("signed corpus requires strict mounted split roots")
        return legacy, legacy, False
    ro = _strict_path(read, READ_ENV)
    rw = _strict_path(write, WRITE_ENV)
    if _within(ro, rw) or _within(rw, ro):
        raise ValueError("Hermes knowledge read and write roots overlap")
    if eval_path is not None:
        evaluation = _strict_path(eval_path, "HERMES_EVAL_PATH")
        if not _within(rw, evaluation) or evaluation == rw:
            raise ValueError("Hermes eval state must live under the write root")
    strict = os.getenv(MOUNT_GUARD_ENV, "")
    if strict not in ("", "false", "true"):
        raise ValueError("invalid Hermes mount isolation mode")
    if strict == "true":
        verify_mount_admission(ro, rw,
            write_mount=os.environ.get(WRITE_MOUNT_ENV, ""))
    signed = os.getenv("HERMES_REQUIRE_SIGNED_CORPUS", "")
    if signed not in ("", "false", "true"):
        raise ValueError("invalid signed corpus admission mode")
    if signed == "true":
        if strict != "true":
            raise ValueError("signed corpus requires kernel mount isolation")
        from assistx.hermes_corpus_custody import verify_signed_corpus
        verify_signed_corpus(ro, rw,
            os.environ.get("HERMES_CORPUS_TRUST_KEY_PATH", ""))
    return ro, rw, True


def safe_model_directory(model: str, *, split: bool) -> str:
    if not split:
        return model.replace("/", "_")
    if len(model) > 128 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", model):
        raise ValueError("unsafe model identifier in split-root mode")
    flat = model.replace("/", "_")
    if flat in (".", "..") or ".." in model.split("/"):
        raise ValueError("model path traversal denied")
    return flat


def safe_knowledge_files(root: str, templates: set[str]) -> Iterator[str]:
    """One-level bounded MD/TXT enumeration without following symlink entries."""
    try:
        top = list(os.scandir(root))
    except OSError:
        return
    for entry in top:
        if entry.is_symlink():
            continue
        try:
            if (entry.is_file(follow_symlinks=False)
                    and entry.name.endswith((".md", ".txt"))
                    and entry.name not in templates):
                yield entry.path
            elif entry.is_dir(follow_symlinks=False):
                for child in os.scandir(entry.path):
                    if (not child.is_symlink()
                            and child.is_file(follow_symlinks=False)
                            and child.name.endswith((".md", ".txt"))):
                        yield child.path
        except OSError:
            continue


def safe_read_text(path: str, limit: int) -> str:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("knowledge input is not a regular file")
        with os.fdopen(fd, "r", errors="ignore") as fh:
            fd = -1
            return fh.read(limit)
    finally:
        if fd >= 0:
            os.close(fd)


def checked_write_path(root: str, path: str) -> str:
    """Deny output-directory or leaf symlink aliases in explicit split mode.

    This check cannot remove races with privileged host actors; Docker must
    independently ensure only the intended scratch mount is writable.
    """
    target = os.path.abspath(path)
    base = os.path.abspath(root)
    if target == base or not _within(base, target):
        raise ValueError("write path escaped the configured scratch root")
    _strict_path(base, "Hermes write root")
    _strict_path(target, "Hermes output destination")
    return target
