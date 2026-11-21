#!/usr/bin/env python3
"""Validate tiny SYNTHETIC-only Hermes knowledge fixture against a pinned manifest.

Never authorize live NAS content, production Hermes access or deployment.
Default invocation is HOLD with zero filesystem scanning or Docker actions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

MAX_FILES = 16
MAX_DIRS = 6
MAX_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 8192
SCRATCH_PARENTS = ("/home/scott/.cache", "/tmp")
SOURCE_PREFIX = "og-hermes-knowledge-"
FILEPART = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN = ("watchdog", "dashcam", "archive", "credential", "secret",
             "private-key", "ssh", "token", "password", ".env", ".git")

class Reject(ValueError):
    pass

def safe_root(root: object) -> Path:
    if not isinstance(root, (str, Path)):
        raise Reject("invalid_fixture_root")
    p = Path(root)
    try:
        if (not p.is_absolute() or p.is_symlink() or not p.is_dir()
                or not p.name.startswith(SOURCE_PREFIX)
                or str(p.resolve(strict=True)) != str(p)
                or p.parent not in tuple(Path(x) for x in SCRATCH_PARENTS)
                or p.is_mount()):
            raise Reject("untrusted_fixture_root")
        s = p.stat()
        if (s.st_uid != os.getuid() or
                stat.S_IMODE(s.st_mode) != 0o700 or not stat.S_ISDIR(s.st_mode)):
            raise Reject("untrusted_fixture_permissions")
        return p
    except OSError as e:
        raise Reject("unavailable_fixture_root") from e

def safe_relpath(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 192:
        return False
    p = PurePosixPath(value)
    if p.is_absolute() or len(p.parts) not in (1, 2, 3):
        return False
    if str(p) != value or any(x in (".", "..") for x in p.parts):
        return False
    for part in p.parts:
        if (not FILEPART.fullmatch(part)
                or part.startswith(".")
                or any(denied in part.lower() for denied in FORBIDDEN)):
            return False
    return True

def parse_manifest(value: object) -> dict[str, tuple[int, str]]:
    if not isinstance(value, dict) or set(value) != {
            "schema", "kind", "generation", "files"}:
        raise Reject("manifest_schema_invalid")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise Reject("manifest_schema_invalid")
    if value["kind"] != "synthetic-hermes-curated-v1":
        raise Reject("production_manifest_denied")
    generation = value["generation"]
    if (not isinstance(generation, str) or
            not re.fullmatch(r"synthetic-[a-z0-9-]{6,44}", generation)):
        raise Reject("invalid_synthetic_generation")
    files = value["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise Reject("manifest_count_invalid")
    output: dict[str, tuple[int, str]] = {}
    total = 0
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise Reject("manifest_file_invalid")
        rel, size, sha = entry["path"], entry["size"], entry["sha256"]
        if not safe_relpath(rel) or rel in output:
            raise Reject("manifest_path_invalid")
        if type(size) is not int or size < 0 or size > MAX_FILE_BYTES:
            raise Reject("manifest_file_size_invalid")
        if not isinstance(sha, str) or not DIGEST.fullmatch(sha):
            raise Reject("manifest_digest_invalid")
        total += size
        if total > MAX_BYTES:
            raise Reject("manifest_total_size_invalid")
        output[rel] = (size, sha)
    return output

def walk_synthetic(root: Path, wanted: dict[str, tuple[int, str]]) -> int:
    pending = [root]
    root_device = root.stat().st_dev
    allowed_dirs = {
        "/".join(name.split("/")[:depth])
        for name in wanted for depth in range(1, len(name.split("/")))
    }
    observed: set[str] = set()
    directory_count = 0
    total = 0
    while pending:
        directory = pending.pop()
        directory_count += 1
        if directory_count > MAX_DIRS:
            raise Reject("directory_limit_exceeded")
        ds = directory.lstat()
        if (not stat.S_ISDIR(ds.st_mode) or ds.st_dev != root_device
                or ds.st_uid != os.getuid() or directory.is_mount()):
            raise Reject("directory_escape_denied")
        with os.scandir(directory) as entries:
            children = list(entries)
        if len(children) > 32:
            raise Reject("directory_entry_limit_exceeded")
        for node in children:
            path = Path(node.path)
            if (node.is_symlink() or
                    path.lstat().st_dev != root_device or
                    path.lstat().st_uid != os.getuid()):
                raise Reject("symlink_or_cross_device_denied")
            relative = path.relative_to(root).as_posix()
            if node.is_dir(follow_symlinks=False):
                if not safe_relpath(relative) or relative not in allowed_dirs:
                    raise Reject("unlisted_directory_denied")
                pending.append(path)
                continue
            if (not node.is_file(follow_symlinks=False)
                    or not safe_relpath(relative)
                    or relative not in wanted):
                raise Reject("unexpected_source_file_denied")
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise Reject("hardlink_or_special_file_denied")
            expected_size, expected_hash = wanted[relative]
            if before.st_size != expected_size:
                raise Reject("file_size_mismatch")
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            fd = os.open(path, flags)
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino, opened.st_size) != (
                        before.st_dev, before.st_ino, before.st_size):
                    raise Reject("file_changed_during_open")
                digest = hashlib.sha256()
                bytes_read = 0
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    total += len(chunk)
                    if (bytes_read > MAX_FILE_BYTES or total > MAX_BYTES
                            or bytes_read > expected_size):
                        raise Reject("fixture_size_limit")
                    digest.update(chunk)
                if bytes_read != expected_size or digest.hexdigest() != expected_hash:
                    raise Reject("content_digest_mismatch")
                after = path.lstat()
                if (opened.st_ino, opened.st_dev, opened.st_size,
                    opened.st_mtime_ns) != (after.st_ino, after.st_dev,
                                           after.st_size, after.st_mtime_ns):
                    raise Reject("file_changed_during_read")
            finally:
                os.close(fd)
            if relative in observed:
                raise Reject("duplicate_entry_denied")
            observed.add(relative)
    if observed != set(wanted):
        raise Reject("manifest_missing_file")
    return total

def audit(root: object = None, manifest: object = None) -> dict:
    result = {
        "schema": 1, "mode": "hermes_synthetic_curated_knowledge_witness",
        "synthetic_curated_fixture_verified": False,
        "files_verified": 0, "logical_bytes_verified": 0,
        "source_is_real_nas": False,
        "independently_signed_corpus_authority": False,
        "production_knowledge_mount_authorized": False,
        "production_containers_modified": False,
        "decision": "HOLD", "violations": [],
    }
    if root is None or manifest is None:
        result["violations"] = ["fixture_not_provided"]
        return result
    try:
        trusted = safe_root(root)   # MUST precede enumeration/content reads.
        wanted = parse_manifest(manifest)
        size = walk_synthetic(trusted, wanted)
        result["files_verified"] = len(wanted)
        result["logical_bytes_verified"] = size
        result["synthetic_curated_fixture_verified"] = True
    except (Reject, OSError, ValueError, TypeError) as e:
        result["violations"] = [str(e) if isinstance(e, Reject)
                                else "fixture_unavailable_or_changed"]
    return result

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--synthetic-fixture-root")
    p.add_argument("--synthetic-manifest")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.synthetic_fixture_root is None or args.synthetic_manifest is None:
        result = audit()
    else:
        # Never load a manifest until the root is admitted as synthetic-only.
        try:
            safe_root(args.synthetic_fixture_root)
            mp = Path(args.synthetic_manifest)
            if (not mp.is_absolute() or mp.parent != Path(args.synthetic_fixture_root).parent
                    or mp.name != Path(args.synthetic_fixture_root).name + ".manifest.json"
                    or not mp.is_file() or mp.is_symlink()
                    or str(mp.resolve(strict=True)) != str(mp)
                    or mp.stat().st_uid != os.getuid()
                    or stat.S_IMODE(mp.stat().st_mode) != 0o600
                    or mp.stat().st_size > MAX_MANIFEST_BYTES):
                raise Reject("manifest_input_untrusted")
            manifest = json.loads(mp.read_text())
            result = audit(args.synthetic_fixture_root, manifest)
        except (Reject, OSError, ValueError, TypeError, json.JSONDecodeError):
            result = audit()
            result["violations"] = ["fixture_input_denied"]
    print(json.dumps(result, sort_keys=True, indent=2 if args.json else None))
    if args.synthetic_fixture_root and not result["synthetic_curated_fixture_verified"]:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
