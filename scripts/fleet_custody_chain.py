"""Fail-closed, append-only, tamper-evident custody observation receipts.

Unsigned SHA-256 chains are not tamper-proof against a privileged attacker.
All historic receipts must be retained, never truncated to a last-N limit.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

MAX_LEDGER_BYTES = 64 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode()


def verify_records(raw):
    previous = "0" * 64
    records = raw.splitlines()
    for line in records:
        item = json.loads(line)
        declared = item.pop("chain_sha256")
        if item.get("previous_sha256") != previous:
            raise ValueError("custody_chain_link_mismatch")
        if digest(canonical(item)) != declared:
            raise ValueError("custody_chain_digest_mismatch")
        previous = declared
    return len(records), previous

def append_receipt(report_path, panel_path, ledger_path):
    report = Path(report_path).read_bytes()
    panel = Path(panel_path).read_bytes()
    ledger = Path(ledger_path)
    if ledger.is_symlink():
        raise ValueError("custody_ledger_is_symlink")
    if ledger.exists() and ledger.stat().st_size > MAX_LEDGER_BYTES:
        raise ValueError("custody_ledger_size_gate_reached_no_truncation")
    before = ledger.read_bytes() if ledger.exists() else b""
    count, previous = verify_records(before)
    record = {
        "schema": "fleet-custody-chain/v1",
        "previous_sha256": previous,
        "report_sha256": digest(report),
        "panel_sha256": digest(panel),
        "report_generated_epoch": json.loads(report)["generated_epoch"],
        "sequence": count + 1,
    }
    record["chain_sha256"] = digest(canonical(record))
    fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_size != len(before):
            raise ValueError("custody_ledger_modified_during_append")
        payload = canonical(record) + b"\n"
        if os.write(fd, payload) != len(payload):
            raise IOError("short_append")
        os.fsync(fd)
    finally:
        os.close(fd)
    return record
