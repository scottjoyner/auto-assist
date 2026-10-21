"""Bounded read-only input for opt-in Hermes *separate* knowledge roots.

This module does not mount storage, grant corpus authority, or authorize
execution. The orchestrator must independently validate the exact RO bind,
its signed source manifest, and distinct RW workspace before enabling tasks.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

MAX_DIR_ENTRIES = 64
MAX_FILES = 32
MAX_FILE_SIZE = 65536
SKIP_NAMES = {"ENV.md", "Home.md", "README.md", "VAULT_INDEX.md"}
BAD_NAME_PARTS = ("watchdog", "dashcam", "credential", "secret", "password", "token")

class ReadScopeDenied(ValueError):
    pass

def _canonical_directory(value: str, *, must_exist: bool) -> Path:
    if not isinstance(value, str) or not value.startswith("/") or ".." in Path(value).parts:
        raise ReadScopeDenied("noncanonical_root")
    root = Path(value)
    try:
        actual = root.resolve(strict=must_exist)
    except (OSError, RuntimeError) as exc:
        raise ReadScopeDenied("unavailable_root") from exc
    if str(actual) != value:
        raise ReadScopeDenied("root_alias_denied")
    if must_exist and not root.is_dir():
        raise ReadScopeDenied("read_root_not_directory")
    return actual

def _existing_directory_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReadScopeDenied("directory_identity_unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ReadScopeDenied("root_not_directory")
    return metadata.st_dev, metadata.st_ino


def _directory_identities(path: Path) -> set[tuple[int, int]]:
    """Record inode/device identity for root and existing non-root ancestors.

    Docker bind mounts can map the SAME host directory to distinct container
    paths, bypassing lexical and symlink checks. Never approve such aliases.
    """
    identities: set[tuple[int, int]] = set()
    for part in (path, *path.parents):
        if part == Path("/"):
            break
        identity = _existing_directory_identity(part)
        if identity is not None:
            identities.add(identity)
    return identities


def validate_separate_roots(read_root: str, write_root: str) -> Path:
    source = _canonical_directory(read_root, must_exist=True)
    destination = _canonical_directory(write_root, must_exist=False)
    if (source == destination or source in destination.parents
            or destination in source.parents):
        raise ReadScopeDenied("read_write_overlap")
    if source == Path("/") or destination == Path("/"):
        raise ReadScopeDenied("filesystem_root_denied")
    source_id = _existing_directory_identity(source)
    destination_id = _existing_directory_identity(destination)
    if (source_id in _directory_identities(destination)
            or (destination_id is not None
                and destination_id in _directory_identities(source))):
        raise ReadScopeDenied("read_write_bind_alias")
    return source

def _eligible_file(entry: os.DirEntry, root_device: int) -> bool:
    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
        return False
    if not entry.name.endswith((".md", ".txt")) or entry.name in SKIP_NAMES:
        return False
    if entry.name.startswith(".") or any(k in entry.name.lower() for k in BAD_NAME_PARTS):
        return False
    metadata = entry.stat(follow_symlinks=False)
    return (stat.S_ISREG(metadata.st_mode) and metadata.st_dev == root_device
            and metadata.st_nlink == 1 and metadata.st_size <= MAX_FILE_SIZE)

def _read_pinned_file(path: Path, root_device: int, limit: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_CLOEXEC
    fd = os.open(str(path), flags)
    try:
        initial = os.fstat(fd)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1
                or initial.st_dev != root_device or initial.st_size > MAX_FILE_SIZE):
            raise ReadScopeDenied("changed_or_unsafe_file")
        chunk = os.read(fd, min(MAX_FILE_SIZE + 1, initial.st_size + 1))
        if len(chunk) != initial.st_size or len(chunk) > MAX_FILE_SIZE:
            raise ReadScopeDenied("source_changed_during_read")
        return chunk.decode("utf-8", errors="ignore")[:limit]
    finally:
        os.close(fd)

def bounded_snapshot(read_root: str, write_root: str, max_chars: int = 2500) -> str:
    """Read at most two levels of small regular files from exact, separate RO root.

    Names/metadata/content remain untrusted. This is a synthetic containment
    primitive, NOT corpus authentication. Does not write source or destination.
    """
    if type(max_chars) is not int or not 0 <= max_chars <= 8192:
        raise ReadScopeDenied("invalid_context_limit")
    source = validate_separate_roots(read_root, write_root)
    if max_chars == 0:
        return ""
    root_device = source.stat().st_dev
    candidates: list[tuple[float, Path]] = []
    def visit(folder: Path, depth: int) -> None:
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError as exc:
            raise ReadScopeDenied("unavailable_source_directory") from exc
        if len(entries) > MAX_DIR_ENTRIES:
            raise ReadScopeDenied("too_many_directory_entries")
        for entry in entries:
            if entry.is_symlink():
                continue
            metadata = entry.stat(follow_symlinks=False)
            if (depth == 0 and entry.is_dir(follow_symlinks=False)
                    and metadata.st_dev == root_device and not entry.name.startswith(".")
                    and not any(k in entry.name.lower() for k in BAD_NAME_PARTS)):
                visit(Path(entry.path), depth + 1)
            elif _eligible_file(entry, root_device):
                candidates.append((metadata.st_mtime, Path(entry.path)))
                if len(candidates) > MAX_FILES:
                    raise ReadScopeDenied("too_many_source_files")
    visit(source, 0)
    chunks: list[str] = []
    total = 0
    for _, path in sorted(candidates, key=lambda item: item[0], reverse=True):
        if total >= max_chars:
            break
        try:
            piece = _read_pinned_file(path, root_device, min(1500, max_chars - total))
        except (OSError, ReadScopeDenied):
            continue
        total += len(piece)
        chunks.append(f"# {path.relative_to(source).as_posix()}\n{piece}")
    return "\n\n".join(chunks)[:max_chars]
