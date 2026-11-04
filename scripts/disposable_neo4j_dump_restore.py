#!/usr/bin/env python3
"""Explicit disposable historical Neo4j dump restore + offline consistency check.

Safety invariants:
- Immutable image pin and single allowlisted live container: neo4j.
- Live /data mounted read-only as an archive source inside disposable Docker;
  ALL database writes go into a unique scratch directory outside live /data.
- No network, no Docker privileged capabilities, fixed commands, resource caps.
- No application data, credentials, paths, stderr, or record values printed.
- Always decision HOLD / exit 3, including perfect restore/consistency.
- Never creates a new production backup, changes live roles or restarts services.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any


IMAGE_RE = re.compile(r"sha256:[a-f0-9]{64}\Z")
DB = "assistx"
MAX_ARCHIVE_BYTES = 3_000_000_000
MAX_EXPANDED_BYTES = 20_000_000_000
MIN_SCRATCH_FREE_GIB = 30
NAME_PREFIX = "assistx-disposable-recovery-"


class RecoveryHold(RuntimeError):
    """Evidence was untrustworthy or restore/consistency failed."""


def _call(argv: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, timeout=timeout, capture_output=True,
                              text=True, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryHold("bounded_command_failed_or_timed_out") from exc


def _inspect(image: str) -> tuple[str, Path]:
    p = _call(["docker", "inspect", "neo4j"], timeout=8)
    if p.returncode != 0 or len(p.stdout) > 256000:
        raise RecoveryHold("neo4j_container_identity_unavailable")
    try:
        objs = json.loads(p.stdout)
        if not isinstance(objs, list) or len(objs) != 1:
            raise RecoveryHold("neo4j_container_identity_ambiguous")
        obj = objs[0]
        if (obj.get("Name") != "/neo4j"
            or obj.get("Image") != image
            or (obj.get("State") or {}).get("Running") is not True):
            raise RecoveryHold("neo4j_runtime_image_or_running_state_changed")
        cid = obj.get("Id")
        if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{64}", cid):
            raise RecoveryHold("neo4j_container_id_invalid")
        mounts = [m for m in obj.get("Mounts") or [] if m.get("Destination") == "/data"]
        if len(mounts) != 1 or mounts[0].get("Type") != "bind":
            raise RecoveryHold("live_graph_data_mount_unverified")
        source = mounts[0].get("Source")
        if not isinstance(source, str):
            raise RecoveryHold("live_data_mount_path_missing")
        path = Path(source)
        if path.is_symlink() or not path.is_dir():
            raise RecoveryHold("live_data_mount_path_untrusted")
        return cid, path.resolve(strict=True)
    except (ValueError, TypeError, KeyError) as exc:
        raise RecoveryHold("docker_inspect_structure_invalid") from exc


def _archive(live: Path, *, max_age_hours: int,
             allow_historical: bool) -> tuple[Path, dict[str, Any]]:
    if not live.is_dir() or live.is_symlink():
        raise RecoveryHold("live_data_mount_not_regular_directory")
    candidates = []
    for root, dirs, files in os.walk(live, followlinks=False):
        depth = len(Path(root).relative_to(live).parts)
        if depth >= 2:
            dirs.clear()
        else:
            if len(dirs) > 30:
                raise RecoveryHold("archive_subdirectory_inventory_incomplete")
            dirs[:] = [d for d in dirs if not (Path(root)/d).is_symlink()]
        if len(files) > 500:
            raise RecoveryHold("archive_file_inventory_incomplete")
        for name in files:
            if name.lower().endswith(".dump"):
                p = Path(root)/name
                if not p.is_symlink() and p.is_file():
                    candidates.append(p)
        if len(candidates) > 3:
            break
    if len(candidates) != 1:
        raise RecoveryHold("archive_missing_or_ambiguous")
    archive = candidates[0]
    if archive.stem != DB or archive.suffix != ".dump":
        raise RecoveryHold("dump_filename_does_not_match_database")
    st = archive.stat()
    if (not stat.S_ISREG(st.st_mode)
        or not 100_000_000 <= st.st_size <= MAX_ARCHIVE_BYTES):
        raise RecoveryHold("archive_file_size_or_type_untrusted")
    age_seconds = time.time() - st.st_mtime
    if age_seconds < -60:
        raise RecoveryHold("archive_timestamp_is_in_future")
    fresh = age_seconds <= max_age_hours * 3600
    if not fresh and not allow_historical:
        raise RecoveryHold("stale_archive_requires_explicit_historical_flag")
    return archive, {
        "archive_size_bytes": st.st_size,
        "archive_age_days": round(age_seconds/86400, 2),
        "within_requested_rpo": fresh,
        "original_stat": (st.st_ino, st.st_size, st.st_mtime_ns),
    }


def _info(stdout: str) -> dict[str, int]:
    # A successful CLI process without output MUST NOT be counted as a
    # valid archive. This caught an actual filename-stem mismatch.
    fields = {}
    for line in stdout.splitlines():
        k, _, v = line.partition(":")
        if k.strip() in ("Database","Format","Files","Bytes"):
            fields[k.strip()] = v.strip()
    if fields.get("Database") != DB or fields.get("Format") != "Neo4j ZSTD Dump.":
        raise RecoveryHold("dump_archive_format_or_database_mismatch")
    try:
        files = int(fields["Files"])
        size = int(fields["Bytes"])
    except (ValueError, TypeError, KeyError) as exc:
        raise RecoveryHold("dump_info_missing_file_or_byte_counts") from exc
    if files < 1 or not 1_000_000 <= size <= MAX_EXPANDED_BYTES:
        raise RecoveryHold("dump_declared_content_size_untrusted")
    return {"declared_file_count":files,"declared_expanded_bytes":size}


def _docker_cmd(image: str, scratch: Path, archive_parent: Path,
                suffix: str) -> list[str]:
    if suffix not in ("info","load","check"):
        raise RecoveryHold("invalid_disposable_phase")
    name = NAME_PREFIX + suffix + "-" + secrets.token_hex(5)
    return [
        "docker","run","--rm","--pull","never","--name",name,
        "--label","assistx.disposable-archive-restore=true",
        "--network","none","--hostname","neo4j-restore-local",
        "--add-host","neo4j-restore-local:127.0.0.1",
        "--read-only","--memory","5g","--cpus","2",
        "--pids-limit","128","--user",f"{os.getuid()}:{os.getgid()}",
        "--cap-drop","ALL","--security-opt","no-new-privileges",
        "--tmpfs","/tmp:rw,exec,size=768m",
        "--mount",f"type=bind,src={archive_parent},dst=/archive,readonly",
        "--mount",f"type=bind,src={scratch/'data'},dst=/data",
        "--mount",f"type=bind,src={scratch/'logs'},dst=/logs",
        "--env","NEO4J_ACCEPT_LICENSE_AGREEMENT=yes",
        "--entrypoint","neo4j-admin",image,
    ]


def _phase(argv: list[str], phase: str, timeout: int) -> subprocess.CompletedProcess[str]:
    p = _call(argv,timeout=timeout)
    if p.returncode != 0:
        # Suppress all raw stderr, which could contain archive source names.
        raise RecoveryHold(f"isolated_{phase}_failed")
    return p


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(2*1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(image: str, *, allow_historical: bool, max_age_hours: int) -> dict[str, Any]:
    if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
        raise RecoveryHold("exact_immutable_image_sha_required")
    if type(max_age_hours) is not int or not 1 <= max_age_hours <= 168:
        raise RecoveryHold("invalid_rpo_age_bound")
    cid, live = _inspect(image)
    archive, meta = _archive(live,max_age_hours=max_age_hours,
                             allow_historical=allow_historical)
    free = os.statvfs(live.parent)
    if free.f_bavail*free.f_frsize < MIN_SCRATCH_FREE_GIB*1073741824:
        raise RecoveryHold("scratch_free_space_insufficient")
    before_sha = _sha256(archive)
    scratch = Path(tempfile.mkdtemp(prefix=NAME_PREFIX,dir=live.parent))
    container_names: list[str] = []
    started = time.monotonic()
    try:
        (scratch/"data").mkdir()
        (scratch/"logs").mkdir()
        if scratch == live or live in scratch.parents:
            raise RecoveryHold("scratch_overlaps_production_data")
        def base(phase: str) -> list[str]:
            return _docker_cmd(image,scratch,archive.parent,phase)

        info_cmd = base("info")
        container_names.append(info_cmd[info_cmd.index("--name")+1])
        result = _phase(info_cmd+[
            "database","load","--info","--from-path=/archive",DB,
        ],"info",40)
        archive_info = _info(result.stdout)
        if (archive_info["declared_expanded_bytes"] >
            free.f_bavail*free.f_frsize/2):
            raise RecoveryHold("archive_expanded_size_exceeds_disk_budget")
        load_cmd = base("load")
        container_names.append(load_cmd[load_cmd.index("--name")+1])
        _phase(load_cmd+[
            "database","load","--from-path=/archive",DB,
        ],"load",190)
        store = scratch/"data"/"databases"/DB
        if not store.is_dir():
            raise RecoveryHold("restore_database_directory_missing")
        restored_files = sum(len(files) for _,_,files in os.walk(store))
        if restored_files < 1:
            raise RecoveryHold("restored_database_store_empty")
        check_cmd = base("check")
        container_names.append(check_cmd[check_cmd.index("--name")+1])
        _phase(check_cmd+[
            "database","check",
            "--from-path-data=/data/databases",
            "--from-path-txn=/data/transactions",
            "--threads=2","--max-off-heap-memory=1G",
            "--check-property-owners=true","--report-path=/logs",DB,
        ],"consistency",220)
        after = archive.stat()
        if (after.st_ino,after.st_size,after.st_mtime_ns) != meta["original_stat"]:
            raise RecoveryHold("archive_changed_during_restore")
        if before_sha != _sha256(archive):
            raise RecoveryHold("archive_hash_changed_during_restore")
        new_cid, new_live = _inspect(image)
        if cid != new_cid or live != new_live:
            raise RecoveryHold("live_neo4j_identity_changed_during_research")
        return {
            "production_decision":"HOLD",
            "cutover_authorized":False,
            "research_disposable_archive_restore":"PASS",
            "offline_full_consistency":"PASS",
            "within_requested_rpo":meta["within_requested_rpo"],
            "archive_age_days":meta["archive_age_days"],
            "archive_size_bytes":meta["archive_size_bytes"],
            "declared_expanded_bytes":archive_info["declared_expanded_bytes"],
            "declared_file_count":archive_info["declared_file_count"],
            "isolated_restored_file_count":restored_files,
            "archive_sha256":before_sha,
            "elapsed_seconds":round(time.monotonic()-started,1),
            "current_recovery_point_verified":False,
            "independent_custody_verified":False,
            "no_production_database_writes":True,
        }
    finally:
        for name in container_names:
            # Only uniquely created disposable research containers are named.
            subprocess.run(["docker","rm","-f",name],timeout=12,
                           capture_output=True,text=True,check=False)
        shutil.rmtree(scratch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-image",required=True)
    parser.add_argument("--explicit-disposable-restore",action="store_true")
    parser.add_argument("--allow-historical",action="store_true")
    parser.add_argument("--max-age-hours",type=int,default=24)
    parser.add_argument("--json",action="store_true",required=True)
    args=parser.parse_args(argv)
    if not args.explicit_disposable_restore:
        parser.error("--explicit-disposable-restore is required")
    try:
        result=verify(args.expected_image,allow_historical=args.allow_historical,
                      max_age_hours=args.max_age_hours)
    except Exception as exc:
        # The exception class/message is never passed through. No stdout
        # or stderr from Docker, Neo4j or any archive is included.
        print(json.dumps({"production_decision":"HOLD",
            "cutover_authorized":False,"research_disposable_archive_restore":"FAIL_OR_UNVERIFIED",
            "error_type":type(exc).__name__},sort_keys=True))
        return 3
    print(json.dumps(result,sort_keys=True))
    return 3  # NEVER a production GO / deployment gate.


if __name__=="__main__":
    sys.exit(main())
