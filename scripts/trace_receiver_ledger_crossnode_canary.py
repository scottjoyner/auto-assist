#!/usr/bin/env python3
"""Disposable LOCAL ext4 receipt-ledger test, not independent WORM acceptance.

Run on the proposed receiver host after copying only these source modules.
Input via stdin: fixture receipt and its verifier public key; NO private keys.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from assistx.trace_asymmetric_custody import GENESIS, receipt_digest
from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_receiver_ledger import append_receiver_receipt, read_receiver_ledger


def run(payload: dict) -> dict:
    receipt = payload["receipt"]
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(payload["witness_public_key"]))
    pinned = {
        "witness_verifier": public,
        "witness_id": "fixture-witness-xwing",
        "producer_id": "fixture-controller",
        "producer_key_id": "fixture-producer-v2",
        "expected_manifest_sha256": "a" * 64,
        "expected_node_id": "xwing",
        "expected_journal_sha256": "b" * 64,
        "expected_index_sha256": "c" * 64,
        "minimum_observed_at_ms": 1_800_200_000_000,
    }
    with tempfile.TemporaryDirectory(prefix=".assistx-receiver-ledger-fixture-", dir=str(Path.home())) as d:
        root = Path(d)
        opts = {
            "root": root,
            **pinned,
            "external_expected_sequence": 1,
            "external_expected_previous_digest": GENESIS,
        }
        first = append_receiver_receipt(receipt=receipt, **opts)
        seq, head = read_receiver_ledger(
            root,
            witness_verifier=public,
            witness_id=pinned["witness_id"],
            producer_id=pinned["producer_id"],
            producer_key_id=pinned["producer_key_id"],
        )
        if seq != 1 or head != receipt_digest(receipt):
            raise AssertionError("persisted signed head inconsistent")
        try:
            append_receiver_receipt(receipt=receipt, **opts)
        except TraceDenied as exc:
            if "receiver_external_anchor_mismatch" not in str(exc):
                raise
        else:
            raise AssertionError("replayed receipt was unexpectedly admitted")
        (root / "receiver-receipts.jsonl").unlink()  # fixture-only adversarial rollback
        try:
            append_receiver_receipt(
                receipt=receipt,
                **{
                    **opts,
                    "external_expected_sequence": 2,
                    "external_expected_previous_digest": head,
                },
            )
        except TraceDenied as exc:
            if "receiver_external_anchor_mismatch" not in str(exc):
                raise
        else:
            raise AssertionError("deleted ledger escaped external anchor")
    if root.exists():
        raise AssertionError("disposable scratch directory persisted")
    return {
        "schema": "assistx.trace-receiver-ledger-physical-fixture.v1",
        "local_fsynced": first["receiver_local_fsynced"],
        "replay_denied": True,
        "deleted_history_denied_by_pinned_external_head": True,
        "scratch_cleaned": True,
        "independent_worm_proven": False,
        "producer_private_key_transferred": False,
        "receiver_private_key_transferred": False,
        "historical_archive_touched": False,
    }


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin)), sort_keys=True))
