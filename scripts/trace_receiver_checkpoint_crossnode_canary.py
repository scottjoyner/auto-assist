#!/usr/bin/env python3
"""Disposable xwing-style checkpoint reconcile canary; no production authority.

stdin: signed synthetic receipt, witness public key, two signed fixture
checkpoints and independently pinned expected checkpoint counter/predecessor.
Does not receive signer private keys, NAS archives or decryption secrets.
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from assistx.trace_asymmetric_custody import GENESIS, receipt_digest
from assistx.trace_execution_adapter import TraceDenied
from assistx.trace_receiver_checkpoint import (
    checkpoint_digest,
    reconcile_local_receipt,
)
from assistx.trace_receiver_ledger import append_receiver_receipt


def run(payload: dict) -> dict:
    witness_public = Ed25519PublicKey.from_public_bytes(base64.b64decode(payload["witness_public"]))
    anchor_public = Ed25519PublicKey.from_public_bytes(base64.b64decode(payload["authority_public"]))
    receipt = payload["receipt"]
    initial = payload["checkpoint_initial"]
    committed = payload["checkpoint_committed"]
    first_head = checkpoint_digest(initial)
    if committed["previous_checkpoint_sha256"] != first_head:
        raise TraceDenied("canary_external_highwater_mismatch")

    with tempfile.TemporaryDirectory(prefix=".assistx-reconcile-scratch-", dir=str(Path.home())) as tmp:
        root = Path(tmp)
        opts = {
            "root": root,
            "authority_public": anchor_public,
            "witness_verifier": witness_public,
            "authority_id": "fixture-anchor-independent",
            "witness_id": "fixture-witness-xwing",
            "producer_id": "fixture-controller",
            "producer_key_id": "fixture-producer-v2",
            "minimum_issued_at_ms": 1_800_200_000_000,
        }
        genesis = {
            **opts,
            "checkpoint": initial,
            "expected_checkpoint_counter": 1,
            "expected_previous_checkpoint_sha256": GENESIS,
        }
        if reconcile_local_receipt(**genesis)["state"] != "SYNCHRONIZED":
            raise AssertionError("genesis reconciliation failed")
        append_receiver_receipt(
            root,
            receipt=receipt,
            witness_verifier=witness_public,
            witness_id=opts["witness_id"],
            producer_id=opts["producer_id"],
            producer_key_id=opts["producer_key_id"],
            external_expected_sequence=1,
            external_expected_previous_digest=GENESIS,
            expected_manifest_sha256="a" * 64,
            expected_node_id="xwing",
            expected_journal_sha256="b" * 64,
            expected_index_sha256="c" * 64,
            minimum_observed_at_ms=1_800_200_000_000,
        )
        pending = reconcile_local_receipt(**genesis)
        if (
            pending["state"] != "PENDING_EXTERNAL_RECONCILIATION"
            or pending["receipt_sha256"] != receipt_digest(receipt)
            or pending["custody_acknowledged"]
        ):
            raise AssertionError("unanchored local receipt incorrectly accepted")
        approved = {
            **opts,
            "checkpoint": committed,
            "expected_checkpoint_counter": 2,
            "expected_previous_checkpoint_sha256": first_head,
        }
        synchronized = reconcile_local_receipt(**approved)
        if synchronized["state"] != "SYNCHRONIZED":
            raise AssertionError("signed external checkpoint did not reconcile")
        (root / "receiver-receipts.jsonl").unlink()  # disposable rollback fixture
        try:
            reconcile_local_receipt(**approved)
        except TraceDenied as exc:
            if "receiver_checkpoint_local_rollback" not in str(exc):
                raise
        else:
            raise AssertionError("local rollback unexpectedly accepted")
    if root.exists():
        raise AssertionError("scratch not cleaned")
    return {
        "schema": "assistx.trace-receiver-checkpoint-physical-canary.v1",
        "signed_genesis_verified": True,
        "unanchored_local_receipt_pending": True,
        "signed_remote_ack_synchronizes": True,
        "local_rollback_against_remote_ack_denied": True,
        "private_keys_transferred": False,
        "independent_worm_proven": False,
        "scratch_cleaned": True,
    }


if __name__ == "__main__":
    import sys

    print(json.dumps(run(json.load(sys.stdin)), sort_keys=True))
