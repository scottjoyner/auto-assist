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


def _within(parent: str, child: str) -> bool:
    return os.path.commonpath((parent, child)) == parent


def resolve_knowledge_roots(legacy: str, eval_path: str | None = None) -> tuple[str, str, bool]:
    read = os.getenv(READ_ENV, "")
    write = os.getenv(WRITE_ENV, "")
    if bool(read) != bool(write):
        raise ValueError("both Hermes knowledge split roots are required")
    if not read:
        return legacy, legacy, False
    ro = _strict_path(read, READ_ENV)
    rw = _strict_path(write, WRITE_ENV)
    if _within(ro, rw) or _within(rw, ro):
        raise ValueError("Hermes knowledge read and write roots overlap")
    if eval_path is not None:
        evaluation = _strict_path(eval_path, "HERMES_EVAL_PATH")
        if not _within(rw, evaluation) or evaluation == rw:
            raise ValueError("Hermes eval state must live under the write root")
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
