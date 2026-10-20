#!/usr/bin/env python3
"""Read-only, offline negative preflight on pinned physical shadow releases.

Never contacts AssistX, creates claims, imports live worker, or writes journals.
Requires the installed executor file to byte-match an independently pinned SHA-256.
Runs only two disabled-flag denials; never tests positive admission.
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
SCHEMA = "assistx.trace-physical-offline-negative.v1"
MAX_AUDIT_BYTES = 16 * 1024 * 1024


def audit_fingerprint(root):
    folder = Path(root)
    try:
        directory = folder.lstat()
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid() or (
            stat.S_IMODE(directory.st_mode) & 0o077
        ):
            return {"status": "unsafe_directory"}
        journal = folder / "journal.jsonl"
        if not os.path.lexists(str(journal)):
            return {"status": "journal_absent"}
        info = journal.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or (
            stat.S_IMODE(info.st_mode) & 0o077
        ) or info.st_size > MAX_AUDIT_BYTES:
            return {"status": "unsafe_or_oversized_journal"}
        digest = hashlib.sha256()
        with journal.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        return {
            "status": "observed", "sha256": digest.hexdigest(),
            "size_bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "inode": info.st_ino,
        }
    except OSError:
        return {"status": "unavailable"}


def collect(release_root, node_id, audit_root, expected_executor_sha256):
    root = Path(release_root)
    path = root / "src/assistx/trace_claim_live_executor.py"
    observed = datetime.now(timezone.utc).isoformat()
    before = audit_fingerprint(audit_root)
    report = {
        "schema": SCHEMA, "observed_at": observed, "node_id": node_id,
        "observed_user_is_root": os.geteuid() == 0,
        "source_digest_match": False,
        "cases": [],
        "audit_before": before, "audit_after": None, "audit_unchanged": False,
        "authenticated_claim_tested": False,
        "network_calls": 0, "claims_issued": 0,
        "production_dispatch_authorized": False, "promotion_eligible": False,
    }
    try:
        if len(expected_executor_sha256) != 64 or any(
            x not in "0123456789abcdef" for x in expected_executor_sha256.lower()
        ):
            raise ValueError("invalid_expected_digest")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        report["source_digest_match"] = digest == expected_executor_sha256.lower()
        if not report["source_digest_match"]:
            raise ValueError("installed_executor_digest_mismatch")
        # Only after checking pinned bytes: import the no-op preflight. Cache writes are disabled.
        sys.path.insert(0, str(root / "src"))
        from assistx.trace_claim_live_executor import preflight
        from assistx.trace_execution_adapter import TraceDenied

        cases = (
            ("both_disabled", {}),
            ("execution_disabled", {"FLEET_TRACE_PROBE_ENABLED": "true",
                                    "FLEET_TRACE_REAL_EXECUTION_ENABLED": "false"}),
        )
        for label, values in cases:
            try:
                preflight(node_id=node_id, audit_root=audit_root, env=values)
                result = "UNEXPECTED_ADMISSION"
            except TraceDenied as exc:
                result = str(exc)
            report["cases"].append({"case": label, "decision": result,
                                    "correct_denial": result == "real_trace_execution_disabled"})
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        report["failure"] = type(exc).__name__
    finally:
        report["audit_after"] = audit_fingerprint(audit_root)
        report["audit_unchanged"] = (
            before == report["audit_after"] and before["status"] == "observed"
        )
    report["offline_negative_pass"] = (
        report["source_digest_match"]
        and len(report["cases"]) == 2
        and all(case["correct_denial"] for case in report["cases"])
        and report["audit_unchanged"]
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--node-id", required=True, choices=("xwing", "scotts-macbook-air"))
    parser.add_argument("--audit-root", required=True)
    parser.add_argument("--expected-executor-sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.release_root, args.node_id, args.audit_root,
                             args.expected_executor_sha256), sort_keys=True))


if __name__ == "__main__":
    main()
