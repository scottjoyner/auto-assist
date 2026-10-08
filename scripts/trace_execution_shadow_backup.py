#!/usr/bin/env python3
"""Independent, encrypted remote trace custody: synthetic shadow journals only.

Fetches via pinned OpenSSH path, verifies journal locally, encrypts before NAS,
decrypt-verifies after NAS, and anchors signed metadata on private local disk.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import hmac
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from trace_execution_shadow_control import load_paths

from assistx.trace_execution_adapter import TraceDenied, TraceReceiptStore

SCHEMA = "assistx.trace-shadow-custody.v1"
DEFAULT_DEST = Path("/nas/desktop-commander-traces/trace-execution-shadow")
DEFAULT_PRIVATE = Path("/home/scott/.config/fleet-trace-shadow")
DEFAULT_ANCHORS = Path("/home/scott/.local/state/fleet-trace-shadow")


def canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_private_dir(path: Path) -> None:
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise TraceDenied("unsafe_private_directory")


def secret_file(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise TraceDenied("unsafe_secret_file")
        value = os.read(fd, 256)
        if len(value.strip()) < 32:
            raise TraceDenied("weak_secret_file")
        return value
    finally:
        os.close(fd)


def fetch_remote(node: dict) -> tuple[bytes, dict]:
    remote = [
        "env",
        "PYTHONPATH=" + node["release_root"] + "/src",
        "python3",
        node["release_root"] + "/scripts/trace_execution_shadow_export.py",
        "--node-id",
        node["node_id"],
        "--audit-root",
        node["audit_root"],
    ]
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
        node["ssh_target"],
        " ".join(shlex.quote(item) for item in remote),
    ]
    result = subprocess.run(command, capture_output=True, timeout=30, check=False)
    if result.returncode or len(result.stdout) > 13 * 1024 * 1024:
        raise TraceDenied("remote_export_failed")
    try:
        envelope = json.loads(result.stdout)
        if envelope.get("schema") != "assistx.trace-shadow-export.v1" or envelope.get("node_id") != node["node_id"]:
            raise TraceDenied("remote_export_identity_invalid")
        raw = base64.b64decode(envelope["journal_base64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise TraceDenied("remote_export_invalid") from exc
    if len(raw) > 8 * 1024 * 1024 or sha(raw) != envelope.get("journal_sha256"):
        raise TraceDenied("remote_export_hash_invalid")
    records = TraceReceiptStore._verify_data(raw)
    if (
        not records
        or len(records) != envelope.get("records")
        or records[-1]["entry_hash"] != envelope.get("last_hash")
        or any(row.get("node_id") != node["node_id"] for row in records)
    ):
        raise TraceDenied("remote_export_chain_invalid")
    return raw, {
        "node_id": node["node_id"],
        "records": len(records),
        "last_hash": records[-1]["entry_hash"],
        "journal_sha256": sha(raw),
    }


def assert_cifs(path: Path) -> None:
    result = subprocess.run(
        ["findmnt", "-n", "-T", str(path), "-o", "FSTYPE"],
        capture_output=True,
        text=True,
        check=True,
    )
    if "cifs" not in result.stdout.split():
        raise TraceDenied("not_verified_cifs_mount")


def decrypt(path: Path, key: Path) -> bytes:
    completed = subprocess.run(
        [
            "gpg",
            "--batch",
            "--quiet",
            "--pinentry-mode",
            "loopback",
            "--passphrase-file",
            str(key),
            "--decrypt",
            str(path),
        ],
        capture_output=True,
        check=False,
        timeout=25,
    )
    if completed.returncode or len(completed.stdout) > 8 * 1024 * 1024:
        raise TraceDenied("encrypted_restore_verification_failed")
    return completed.stdout


def encrypt(raw: bytes, output: Path, key: Path) -> None:
    result = subprocess.run(
        [
            "gpg",
            "--batch",
            "--quiet",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase-file",
            str(key),
            "--cipher-algo",
            "AES256",
            "--symmetric",
            "--output",
            str(output),
        ],
        input=raw,
        capture_output=True,
        check=False,
        timeout=25,
    )
    if result.returncode:
        raise TraceDenied("encryption_failed")


def anchor(manifest: dict, anchor_key: bytes, dest: Path) -> dict:
    ensure_private_dir(dest)
    path = dest / "signed-heads.jsonl"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or st.st_nlink != 1:
            raise TraceDenied("anchor_file_unsafe")
        fcntl.flock(fd, fcntl.LOCK_EX)
        size = os.fstat(fd).st_size
        if size > 8 * 1024 * 1024:
            raise TraceDenied("anchor_index_too_large")
        os.lseek(fd, 0, os.SEEK_SET)
        payload = os.read(fd, size)
        prior = ""
        if payload and not payload.endswith(b"\n"):
            raise TraceDenied("anchor_index_torn")
        for line in payload.splitlines():
            row = json.loads(line)
            digest = row.pop("signature", "")
            if (
                not hmac.compare_digest(digest, hmac.new(anchor_key, canonical(row), hashlib.sha256).hexdigest())
                or row.get("previous_signature") != prior
            ):
                raise TraceDenied("anchor_index_invalid")
            prior = digest
        signed = {
            "schema": SCHEMA,
            "previous_signature": prior,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            **manifest,
        }
        signed["signature"] = hmac.new(anchor_key, canonical(signed), hashlib.sha256).hexdigest()
        os.lseek(fd, 0, os.SEEK_END)
        data = canonical(signed) + b"\n"
        if os.write(fd, data) != len(data):
            raise TraceDenied("anchor_short_write")
        os.fsync(fd)
        dirfd = os.open(dest, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        return signed
    finally:
        os.close(fd)


def verify_continuation(raw: bytes, *, node_id: str, anchors: Path, key: bytes) -> None:
    """Reject remote journal rollback or replacement against last signed head."""
    path = anchors / "signed-heads.jsonl"
    if not path.exists():
        return
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise TraceDenied("unsafe_anchor_index")
        fcntl.flock(fd, fcntl.LOCK_SH)
        payload = os.read(fd, min(st.st_size + 1, 8 * 1024 * 1024 + 1))
    finally:
        os.close(fd)
    if not payload.endswith(b"\n") or len(payload) > 8 * 1024 * 1024:
        raise TraceDenied("anchor_index_torn_or_too_large")
    previous_signature = ""
    previous = None
    for raw_line in payload.splitlines():
        signed = json.loads(raw_line)
        signature = signed.pop("signature", "")
        if signed.get("previous_signature") != previous_signature or not hmac.compare_digest(
            signature, hmac.new(key, canonical(signed), hashlib.sha256).hexdigest()
        ):
            raise TraceDenied("anchor_index_invalid")
        previous_signature = signature
        previous = signed
    if previous is None:
        return
    if previous.get("node_id") != node_id:
        raise TraceDenied("anchor_node_mismatch")
    records = TraceReceiptStore._verify_data(raw)
    count = previous["records"]
    if (
        not isinstance(count, int)
        or count < 1
        or len(records) < count
        or records[count - 1]["entry_hash"] != previous["last_hash"]
    ):
        raise TraceDenied("remote_journal_rollback_or_rewrite")


def backup(node: dict, *, private: Path, anchors: Path, destination: Path) -> dict:
    ensure_private_dir(private)
    ensure_private_dir(anchors)
    node_private = private / node["node_id"]
    node_anchors = anchors / node["node_id"]
    ensure_private_dir(node_private)
    ensure_private_dir(node_anchors)
    encryption_key = node_private / "encryption.passphrase"
    signing_key = secret_file(node_private / "anchor.key")
    secret_file(encryption_key)  # permissions/strength check; do not print secret
    assert_cifs(destination.parent if not destination.exists() else destination)
    # Losing the signed local ledger must not silently reset custody history.
    if not (node_anchors / "signed-heads.jsonl").exists():
        existing_dir = destination / node["node_id"]
        if existing_dir.is_dir() and any(existing_dir.glob("*.jsonl.gpg")):
            raise TraceDenied("anchor_ledger_missing_with_archives_present")
    raw, info = fetch_remote(node)
    verify_continuation(raw, node_id=node["node_id"], anchors=node_anchors, key=signing_key)
    if shutil.disk_usage(destination.parent).free < 100 * 1024 * 1024:
        raise TraceDenied("nas_capacity_reserve")
    with tempfile.TemporaryDirectory(prefix="shadow-trace-", dir=str(node_anchors)) as temp:
        tmp = Path(temp)
        ciphertext = tmp / "trace.jsonl.gpg"
        encrypt(raw, ciphertext, encryption_key)
        if decrypt(ciphertext, encryption_key) != raw:
            raise TraceDenied("local_roundtrip_mismatch")
        enc_hash = sha(ciphertext.read_bytes())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{node['node_id']}-{stamp}-{enc_hash[:12]}.jsonl.gpg"
        target_dir = destination / node["node_id"]
        target_dir.mkdir(parents=True, exist_ok=True)
        assert_cifs(target_dir)
        landed = target_dir / filename
        if landed.exists():
            raise TraceDenied("archive_collision")
        partial = target_dir / ("." + filename + ".partial")
        if partial.exists():
            raise TraceDenied("stale_archive_partial")
        try:
            shutil.copyfile(ciphertext, partial)
            if sha(partial.read_bytes()) != enc_hash or decrypt(partial, encryption_key) != raw:
                raise TraceDenied("nas_roundtrip_mismatch")
            os.replace(partial, landed)
        finally:
            if partial.exists():
                partial.unlink()
    meta = {
        **info,
        "encrypted_sha256": enc_hash,
        "archive": str(landed.relative_to(destination)),
        "bytes": landed.stat().st_size,
        "algorithm": "gpg-aes256-symmetric",
        "restore_checked": True,
    }
    anchored = anchor(meta, signing_key, node_anchors)
    # Signed sidecar can be checked using the local-only signing key.
    sidecar = landed.with_name(landed.name + ".manifest.json")
    if sidecar.exists():
        raise TraceDenied("manifest_collision")
    sidecar.write_bytes(canonical(anchored) + b"\n")
    return {**meta, "signed_head": anchored["signature"]}


def verify_archives(node: dict, *, private: Path, anchors: Path, destination: Path) -> dict:
    """Independent offline restore check for every signed snapshot of one node."""
    node_id = node["node_id"]
    node_private = private / node_id
    node_anchors = anchors / node_id
    ensure_private_dir(node_private)
    ensure_private_dir(node_anchors)
    key = node_private / "encryption.passphrase"
    signing_key = secret_file(node_private / "anchor.key")
    secret_file(key)
    assert_cifs(destination if destination.exists() else destination.parent)
    ledger = node_anchors / "signed-heads.jsonl"
    fd = os.open(ledger, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise TraceDenied("unsafe_anchor_index")
        if st.st_size > 8 * 1024 * 1024:
            raise TraceDenied("anchor_index_too_large")
        fcntl.flock(fd, fcntl.LOCK_SH)
        payload = os.read(fd, st.st_size + 1)
    finally:
        os.close(fd)
    if not payload or not payload.endswith(b"\n"):
        raise TraceDenied("anchor_index_missing_or_torn")
    previous_sig = ""
    verified = 0
    latest = None
    for line in payload.splitlines():
        signed = json.loads(line)
        unsigned = dict(signed)
        sig = unsigned.pop("signature", "")
        if (
            unsigned.get("schema") != SCHEMA
            or unsigned.get("previous_signature") != previous_sig
            or not hmac.compare_digest(sig, hmac.new(signing_key, canonical(unsigned), hashlib.sha256).hexdigest())
        ):
            raise TraceDenied("archive_anchor_signature_invalid")
        if unsigned.get("node_id") != node_id:
            raise TraceDenied("archive_anchor_node_mismatch")
        relative = unsigned["archive"]
        if (
            not isinstance(relative, str)
            or not relative.startswith(node_id + "/")
            or Path(relative).suffix != ".gpg"
            or ".." in Path(relative).parts
        ):
            raise TraceDenied("archive_path_invalid")
        landed = destination / relative
        sidecar = landed.with_name(landed.name + ".manifest.json")
        if not sidecar.is_file() or sidecar.read_bytes() != canonical(signed) + b"\n":
            raise TraceDenied("archive_manifest_mismatch")
        if sha(landed.read_bytes()) != unsigned["encrypted_sha256"]:
            raise TraceDenied("archive_ciphertext_hash_mismatch")
        raw = decrypt(landed, key)
        if sha(raw) != unsigned["journal_sha256"]:
            raise TraceDenied("archive_journal_hash_mismatch")
        records = TraceReceiptStore._verify_data(raw)
        if (
            len(records) != unsigned["records"]
            or not records
            or records[-1]["entry_hash"] != unsigned["last_hash"]
            or any(r.get("node_id") != node_id for r in records)
        ):
            raise TraceDenied("archive_journal_chain_invalid")
        previous_sig = sig
        latest = unsigned
        verified += 1
    return {
        "node_id": node_id,
        "archives_verified": verified,
        "last_hash": latest["last_hash"],
        "latest_records": latest["records"],
        "signed_head": previous_sig,
        "restore_checked": True,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--node", required=True)
    p.add_argument("--private", type=Path, default=DEFAULT_PRIVATE)
    p.add_argument("--anchors", type=Path, default=DEFAULT_ANCHORS)
    p.add_argument("--destination", type=Path, default=DEFAULT_DEST)
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--verify-remote-only", action="store_true")
    modes.add_argument("--verify-archives-only", action="store_true")
    args = p.parse_args()
    try:
        nodes = load_paths(args.config)
        if args.node not in nodes:
            raise TraceDenied("unregistered_node")
        if args.verify_remote_only:
            _, meta = fetch_remote(nodes[args.node])
            result = {"ok": True, "encrypted": False, **meta}
        elif args.verify_archives_only:
            result = {
                "ok": True,
                **verify_archives(
                    nodes[args.node],
                    private=args.private,
                    anchors=args.anchors,
                    destination=args.destination,
                ),
            }
        else:
            result = {
                "ok": True,
                "encrypted": True,
                **backup(
                    nodes[args.node],
                    private=args.private,
                    anchors=args.anchors,
                    destination=args.destination,
                ),
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except (TraceDenied, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "node_id": args.node, "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
