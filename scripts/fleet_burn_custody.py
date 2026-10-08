#!/usr/bin/env python3
"""Bounded read-only fleet OpenCode archive custody inventory.

Manifest/size/hostname checks establish inventory coverage, NOT a proof of
session-level usage, free entitlement, archive contents or vendor billing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "fleet-burn-custody/v1"
ARCHIVE_PATTERN = re.compile(r"^opencode_[A-Za-z0-9_.-]+_[0-9]{8}T[0-9]{6}Z\.db\.zst$")
HEX_SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 32 * 1024
MAX_LATEST_BYTES = 256
DEFAULT_NODES = (
    "x1-370", "xwing", "deathstar-XPS-8920", "scott-OptiPlex-9030-AIO",
    "scott-Lenovo-ideapad-330S-15IKB", "destroyer",
)


def _utc_stamp(value):
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _failed(name, issue):
    return {"node": name, "status": "QUARANTINED", "reason": issue,
            "harvest_epoch": None, "archive_sha256_claim": None,
            "archive_hash_verified": False, "session_count_claim": None,
            "usable_for_token_aggregation": False}


def inspect_node(root: Path, node: str, *, now: float, freshness_hours=36,
                 max_sha_bytes=0):
    """Never follow a user-controlled latest.txt into another directory."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", node) or node in (".", ".."):
        return _failed(str(node)[:100], "invalid_node_identity")
    directory = root / node
    latest = directory / "latest.txt"
    try:
        if not directory.is_dir() or not latest.is_file():
            return _failed(node, "missing_latest")
        if latest.stat().st_size > MAX_LATEST_BYTES:
            return _failed(node, "oversized_latest")
        filename = latest.read_text(encoding="utf-8").strip()
        if not ARCHIVE_PATTERN.fullmatch(filename):
            return _failed(node, "invalid_latest_filename")
        archive = directory / filename
        manifest = directory / (filename + ".manifest.json")
        if not archive.is_file() or not manifest.is_file():
            return _failed(node, "archive_or_manifest_missing")
        if archive.is_symlink() or manifest.is_symlink() or latest.is_symlink():
            return _failed(node, "symlink_custody_path")
        if manifest.stat().st_size > MAX_MANIFEST_BYTES:
            return _failed(node, "oversized_manifest")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("host") != node:
            return _failed(node, "host_identity_mismatch")
        if data.get("archive") != filename:
            return _failed(node, "manifest_filename_mismatch")
        size = data.get("archive_bytes")
        if type(size) is not int or size <= 0 or archive.stat().st_size != size:
            return _failed(node, "archive_size_mismatch")
        digest = data.get("archive_sha256")
        if not isinstance(digest, str) or not HEX_SHA.fullmatch(digest):
            return _failed(node, "invalid_archive_digest")
        if data.get("ok") is not True or data.get("quick_check") != "ok":
            return _failed(node, "upstream_snapshot_health_unverified")
        timestamp = _utc_stamp(data.get("stamp_utc"))
        if timestamp is None or timestamp > now + 300:
            return _failed(node, "invalid_snapshot_timestamp")
        stats = data.get("source_stats")
        count = (stats.get("session") or {}).get("rows") if isinstance(stats, dict) else None
        if type(count) is not int or count < 0:
            return _failed(node, "invalid_session_count_claim")
        verified = False
        if 0 < size <= max_sha_bytes:
            h = hashlib.sha256()
            with archive.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(block)
            if h.hexdigest() != digest:
                return _failed(node, "archive_sha256_mismatch")
            verified = True
        age_hours = round((now - timestamp) / 3600, 1)
        stale = age_hours > freshness_hours
        return {"node": node, "status": "STALE" if stale else "MANIFEST_CHECKED",
                "reason": "snapshot_exceeds_freshness_window" if stale else None,
                "harvest_epoch": timestamp, "age_hours": age_hours,
                "archive_bytes": size, "archive_sha256_claim": digest,
                "archive_hash_verified": verified, "quick_check_claim": "ok",
                "session_count_claim": count, "usable_for_token_aggregation": False}
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return _failed(node, "custody_read_error")


def build_report(root: Path, nodes=DEFAULT_NODES, *, now=None,
                 freshness_hours=36, max_sha_bytes=0):
    now = time.time() if now is None else float(now)
    if not isinstance(nodes, (list, tuple)) or not nodes or len(nodes) > 64:
        raise ValueError("fleet roster must contain 1–64 nodes")
    if len(set(nodes)) != len(nodes):
        raise ValueError("duplicate physical node names in roster")
    rows = [inspect_node(root, node, now=now, freshness_hours=freshness_hours,
                         max_sha_bytes=max_sha_bytes) for node in nodes]
    # Same claimed digest across hosts may be a copied snapshot, NOT independent
    # consumption: preserve both custody rows but never sum session counts.
    digest_hosts = {}
    for row in rows:
        digest = row.get("archive_sha256_claim")
        if digest:
            digest_hosts.setdefault(digest, []).append(row["node"])
    collisions = [names for names in digest_hosts.values() if len(names) > 1]
    verified = sum(r["archive_hash_verified"] for r in rows)
    statuses = {s: sum(r["status"] == s for r in rows)
                for s in ("MANIFEST_CHECKED", "STALE", "QUARANTINED")}
    return {
        "schema": SCHEMA, "generated_epoch": now, "read_only": True,
        "custody_root_identity": "fleet-opencode-archive",
        "expected_nodes": len(nodes), "observed_manifests":
            statuses["MANIFEST_CHECKED"] + statuses["STALE"],
        "content_hash_verified_nodes": verified,
        "stale_nodes": statuses["STALE"], "quarantined_nodes": statuses["QUARANTINED"],
        "digest_reuse_groups": [sorted(names) for names in collisions],
        "actual_fleet_token_count": None,
        "token_aggregation_qualified": False,
        "archive_integrity_verified_for_all": verified == len(nodes),
        "notes": ("A manifest is only a claim until its archive bytes are hashed. "
                  "Snapshot session counts are NOT token counts. No cross-node "
                  "usage summation without a verified per-event export and "
                  "session/part deduplication."),
        "nodes": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only fleet harvest custody gate")
    parser.add_argument("--root", type=Path, default=Path("/nas/opencode-fleet"))
    parser.add_argument("--node", action="append", dest="nodes")
    parser.add_argument("--freshness-hours", type=float, default=36)
    parser.add_argument("--verify-max-mib", type=int, default=0,
                        help="Hash only archives this size or smaller; default 0")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if not 0 <= args.verify_max_mib <= 64:
        parser.error("--verify-max-mib must be 0..64")
    if not 1 <= args.freshness_hours <= 720:
        parser.error("--freshness-hours must be 1..720")
    result = build_report(args.root, args.nodes or DEFAULT_NODES,
                          freshness_hours=args.freshness_hours,
                          max_sha_bytes=args.verify_max_mib * 1024 * 1024)
    # Only output to the requested private destination; never write to NAS.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    import os
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=".fleet-custody-", dir=args.out.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(result, target, indent=2, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, args.out)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({"schema": SCHEMA, "expected_nodes": result["expected_nodes"],
                      "manifest_present": result["observed_manifests"],
                      "stale": result["stale_nodes"],
                      "quarantined": result["quarantined_nodes"],
                      "archive_hash_verified_nodes": result["content_hash_verified_nodes"],
                      "qualified_for_tokens": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
