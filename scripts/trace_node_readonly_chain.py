#!/usr/bin/env python3
"""Verify physical trace journal bytes with pinned offline chain logic.

Read-only. No file opened for writing, no network, no key material, no workers.
Does not prove external WORM custody, write durability or production authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
MAX_READ = 16 * 1024 * 1024


def audit_bytes(root):
    folder = Path(root)
    st = folder.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError("audit_root_unsafe")
    path = folder / "journal.jsonl"
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
        or before.st_mode & 0o077 or before.st_nlink != 1
        or before.st_size > MAX_READ):
        raise ValueError("audit_journal_unsafe")
    with path.open("rb") as f:
        data = f.read(MAX_READ + 1)
    after = path.lstat()
    def identity(item):
        return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if identity(before) != identity(after) or len(data) != after.st_size:
        raise ValueError("audit_journal_changed_during_read")
    return data, identity(before)


def collect(release_root, audit_root, node_id, expected_sha):
    report = {
        "schema": "assistx.trace-physical-readonly-chain.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "node_id": node_id, "source_digest_match": False,
        "journal_sha256": None, "record_count": None, "head_sha256": None,
        "chain_valid": False, "journal_unchanged": False,
        "independent_worm_witness_verified": False,
        "no_file_writes": True, "network_calls": 0,
        "production_dispatch_authorized": False, "promotion_eligible": False,
    }
    try:
        path = Path(release_root) / "src/assistx/trace_execution_adapter.py"
        if len(expected_sha) != 64 or any(c not in "0123456789abcdef" for c in expected_sha.lower()):
            raise ValueError("invalid_expected_digest")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        report["source_digest_match"] = actual == expected_sha.lower()
        if not report["source_digest_match"]:
            raise ValueError("chain_verifier_source_drift")
        data, before = audit_bytes(audit_root)
        report["journal_sha256"] = hashlib.sha256(data).hexdigest()
        sys.path.insert(0, str(Path(release_root) / "src"))
        from assistx.trace_execution_adapter import TraceReceiptStore
        rows = TraceReceiptStore._verify_data(data)  # memory-only parser; NO O_RDWR/flock
        if not rows or any(row.get("node_id") != node_id for row in rows):
            raise ValueError("audit_wrong_node_or_empty")
        report["record_count"] = len(rows)
        report["head_sha256"] = rows[-1]["entry_hash"]
        report["chain_valid"] = True
        _, after = audit_bytes(audit_root)
        report["journal_unchanged"] = before == after
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        report["error_kind"] = type(exc).__name__
    except Exception as exc:
        # TraceDenied (defined in imported execution adapter) is a controlled
        # integrity denial, but never expose exception text or journal contents.
        report["error_kind"] = type(exc).__name__
    report["readonly_chain_pass"] = bool(
        report["source_digest_match"] and report["chain_valid"]
        and report["journal_unchanged"]
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--audit-root", required=True)
    parser.add_argument("--node-id", required=True, choices=("xwing", "scotts-macbook-air"))
    parser.add_argument("--expected-adapter-sha256", required=True)
    args = parser.parse_args()
    report = collect(args.release_root, args.audit_root, args.node_id, args.expected_adapter_sha256)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
