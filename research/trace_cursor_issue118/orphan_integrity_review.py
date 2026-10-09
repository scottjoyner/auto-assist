"""Read-only archived redacted-member verification for issue #118.

Does not write sidecars, adopt checkpoints, print source paths, or authorize recovery.
"""
from __future__ import annotations
import hashlib
import json
import pathlib
import re
import subprocess

MAX_ARCHIVE_BYTES=64*1024*1024
MAX_MEMBER_BYTES=1*1024*1024
MAX_MANIFEST_BYTES=1024*1024
MAX_ROWS=256
_SAFE_BASENAME=re.compile(r"[A-Za-z0-9_.-]{1,160}\Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_member(archive: pathlib.Path, basename: str, max_bytes=MAX_MEMBER_BYTES) -> bytes:
    if basename != "manifest.json" and _SAFE_BASENAME.fullmatch(basename) is None:
        raise ValueError("unsafe archive member name")
    if basename == "manifest.json" and max_bytes>MAX_MANIFEST_BYTES:
        raise ValueError("unbounded manifest")
    p=subprocess.run(["timeout","15","tar","-I","zstd","-xOf",str(archive),
                      "./"+basename],capture_output=True,timeout=18,check=False)
    if p.returncode!=0 or len(p.stdout)>max_bytes:
        raise ValueError("member missing, invalid or over research limit")
    return p.stdout


def verify_archive(archive: pathlib.Path, *, control_manifest: pathlib.Path|None=None,
                   verify_members: bool=True) -> dict:
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("not a regular archive")
    if archive.stat().st_size>MAX_ARCHIVE_BYTES:
        raise ValueError("archive beyond bounded review budget")
    compressed=archive.read_bytes()
    digest=sha256(compressed)
    if not archive.name.endswith("-"+digest[:8]+".tar.zst"):
        raise ValueError("archive filename digest prefix mismatch")
    inner=json.loads(read_member(archive,"manifest.json",MAX_MANIFEST_BYTES))
    if type(inner) is not dict or type(inner.get("files")) is not list:
        raise ValueError("invalid internal archive manifest")
    rows=inner["files"]
    if len(rows)>MAX_ROWS:
        raise ValueError("too many archived source rows")
    checked=0
    for row in rows:
        if type(row) is not dict:
            raise ValueError("malformed manifest file entry")
        basename=row.get("basename")
        if type(basename) is not str:
            raise ValueError("missing basename")
        if verify_members:
            content=read_member(archive,basename)
            if sha256(content)!=row.get("sha256") or len(content)!=row.get("bytes_out"):
                raise ValueError("archived source hash or size mismatch")
            checked+=1
    external=dict(inner,archive=archive.name,
                  archive_bytes=len(compressed),archive_sha256=digest)
    same_as_control=None
    if control_manifest is not None:
        if control_manifest.stat().st_size>MAX_MANIFEST_BYTES:
            raise ValueError("oversized external control")
        existing=json.loads(control_manifest.read_text(encoding="utf-8"))
        same_as_control=(external==existing)
    return {
        "archive_name_hash":sha256(archive.name.encode()),
        "sha256_and_size_verified_members":checked,
        "member_count":len(rows),
        "source_members_verified":verify_members,
        "database_rows_declared":len(inner.get("databases",[])),
        "legacy_without_committed_end":any(
             "offset_end_committed" not in row for row in rows),
        "reconstructed_sidecar_matches_control":same_as_control,
        "live_sidecar_created":False,
        "archive_bytes_written":0,
    }