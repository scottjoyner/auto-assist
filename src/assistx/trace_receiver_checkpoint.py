"""Read-only reconciliation against a separately signed witness high-water head.

Research-only. Signatures do not make mutable files WORM. Trusted
expected checkpoint counter and predecessor MUST come from an independent
non-rewindable custodian, not from this signed checkpoint or local ledger.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .trace_asymmetric_custody import GENESIS, receipt_digest
from .trace_execution_adapter import TraceDenied
from .trace_receiver_ledger import MAX_LEDGER, MAX_RECEIPT, NAME, _lock, read_receiver_ledger
from .trace_segment_bundle import _private, _read
from .trace_segment_plan import canonical, digest

SCHEMA = "assistx.trace-receiver-external-checkpoint.v1"
_FIELDS = frozenset(
    {
        "schema",
        "algorithm",
        "authority_id",
        "witness_id",
        "producer_id",
        "producer_key_id",
        "receipt_sequence",
        "receipt_sha256",
        "checkpoint_counter",
        "previous_checkpoint_sha256",
        "issued_at_ms",
    }
)
_ID = re.compile(r"[A-Za-z0-9_.:@-]{1,120}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_SIG = re.compile(r"[A-Za-z0-9_-]{86}\Z")


def _identity(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise TraceDenied("receiver_checkpoint_identity_invalid")
    return value


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise TraceDenied("receiver_checkpoint_digest_invalid")
    return value


def _int(value: Any, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise TraceDenied("receiver_checkpoint_integer_invalid")
    return value


def checkpoint_digest(value: dict[str, Any]) -> str:
    if not isinstance(value, dict) or set(value) != _FIELDS | {"signature"}:
        raise TraceDenied("receiver_checkpoint_schema_invalid")
    return digest(canonical(value))


def sign_fixture_checkpoint(
    *,
    authority_private: Ed25519PrivateKey,
    authority_id: str,
    witness_id: str,
    producer_id: str,
    producer_key_id: str,
    receipt_sequence: int,
    receipt_sha256: str,
    checkpoint_counter: int,
    previous_checkpoint_sha256: str,
    issued_at_ms: int,
) -> dict[str, Any]:
    """Test-only signing contract; requires an independently managed signer."""
    for value in (authority_id, witness_id, producer_id, producer_key_id):
        _identity(value)
    _int(receipt_sequence, 0)
    _int(checkpoint_counter, 1)
    _int(issued_at_ms, 1)
    _sha(receipt_sha256)
    _sha(previous_checkpoint_sha256)
    if receipt_sequence == 0 and receipt_sha256 != GENESIS:
        raise TraceDenied("receiver_checkpoint_genesis_receipt_invalid")
    if checkpoint_counter == 1 and previous_checkpoint_sha256 != GENESIS:
        raise TraceDenied("receiver_checkpoint_genesis_anchor_invalid")
    if not isinstance(authority_private, Ed25519PrivateKey):
        raise TraceDenied("receiver_checkpoint_signer_invalid")
    unsigned = {
        "schema": SCHEMA,
        "algorithm": "Ed25519",
        "authority_id": authority_id,
        "witness_id": witness_id,
        "producer_id": producer_id,
        "producer_key_id": producer_key_id,
        "receipt_sequence": receipt_sequence,
        "receipt_sha256": receipt_sha256,
        "checkpoint_counter": checkpoint_counter,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "issued_at_ms": issued_at_ms,
    }
    signature = base64.urlsafe_b64encode(authority_private.sign(canonical(unsigned))).decode("ascii").rstrip("=")
    return {**unsigned, "signature": signature}


def verify_checkpoint(
    checkpoint: dict[str, Any],
    *,
    authority_public: Ed25519PublicKey,
    authority_id: str,
    witness_id: str,
    producer_id: str,
    producer_key_id: str,
    expected_checkpoint_counter: int,
    expected_previous_checkpoint_sha256: str,
    minimum_issued_at_ms: int,
) -> str:
    """Reject stale and foreign checkpoints against independent prior anchor."""
    if not isinstance(checkpoint, dict) or set(checkpoint) != _FIELDS | {"signature"}:
        raise TraceDenied("receiver_checkpoint_schema_invalid")
    unsigned = {k: v for k, v in checkpoint.items() if k != "signature"}
    if unsigned["schema"] != SCHEMA or unsigned["algorithm"] != "Ed25519":
        raise TraceDenied("receiver_checkpoint_schema_invalid")
    for field in ("authority_id", "witness_id", "producer_id", "producer_key_id"):
        _identity(unsigned[field])
    _sha(unsigned["receipt_sha256"])
    _sha(unsigned["previous_checkpoint_sha256"])
    for name, minimum in (("receipt_sequence", 0), ("checkpoint_counter", 1), ("issued_at_ms", 1)):
        _int(unsigned[name], minimum)
    _int(expected_checkpoint_counter, 1)
    _int(minimum_issued_at_ms, 0)
    _sha(expected_previous_checkpoint_sha256)
    if unsigned["receipt_sequence"] == 0 and unsigned["receipt_sha256"] != GENESIS:
        raise TraceDenied("receiver_checkpoint_genesis_receipt_invalid")
    if (
        unsigned["authority_id"] != authority_id
        or unsigned["witness_id"] != witness_id
        or unsigned["producer_id"] != producer_id
        or unsigned["producer_key_id"] != producer_key_id
    ):
        raise TraceDenied("receiver_checkpoint_authority_mismatch")
    if (
        unsigned["checkpoint_counter"] != expected_checkpoint_counter
        or unsigned["previous_checkpoint_sha256"] != expected_previous_checkpoint_sha256
        or unsigned["issued_at_ms"] < minimum_issued_at_ms
    ):
        raise TraceDenied("receiver_checkpoint_replay_or_rollback")
    if expected_checkpoint_counter == 1 and expected_previous_checkpoint_sha256 != GENESIS:
        raise TraceDenied("receiver_checkpoint_genesis_anchor_invalid")
    if not isinstance(authority_public, Ed25519PublicKey):
        raise TraceDenied("receiver_checkpoint_public_key_invalid")
    signature = checkpoint["signature"]
    if not isinstance(signature, str) or not _SIG.fullmatch(signature):
        raise TraceDenied("receiver_checkpoint_signature_invalid")
    try:
        authority_public.verify(base64.urlsafe_b64decode(signature + "=="), canonical(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise TraceDenied("receiver_checkpoint_signature_invalid") from exc
    return checkpoint_digest(checkpoint)


def reconcile_local_receipt(
    root: Path,
    *,
    checkpoint: dict[str, Any],
    authority_public: Ed25519PublicKey,
    witness_verifier: Ed25519PublicKey,
    authority_id: str,
    witness_id: str,
    producer_id: str,
    producer_key_id: str,
    expected_checkpoint_counter: int,
    expected_previous_checkpoint_sha256: str,
    minimum_issued_at_ms: int,
) -> dict[str, Any]:
    """Classify only. Never writes or submits an external ACK.

    A single durable local receipt ahead of the independent checkpoint is
    a PENDING_EXTERNAL_RECONCILIATION result, NOT a custody success.
    """
    anchor = verify_checkpoint(
        checkpoint,
        authority_public=authority_public,
        authority_id=authority_id,
        witness_id=witness_id,
        producer_id=producer_id,
        producer_key_id=producer_key_id,
        expected_checkpoint_counter=expected_checkpoint_counter,
        expected_previous_checkpoint_sha256=expected_previous_checkpoint_sha256,
        minimum_issued_at_ms=minimum_issued_at_ms,
    )
    _private(root)
    lock = _lock(root)
    try:
        seq, head = read_receiver_ledger(
            root,
            witness_verifier=witness_verifier,
            witness_id=witness_id,
            producer_id=producer_id,
            producer_key_id=producer_key_id,
        )
        remote_seq = checkpoint["receipt_sequence"]
        remote_head = checkpoint["receipt_sha256"]
        if seq == remote_seq:
            if head != remote_head:
                raise TraceDenied("receiver_checkpoint_conflicting_head")
            return {
                "state": "SYNCHRONIZED",
                "local_sequence": seq,
                "receipt_sha256": head,
                "checkpoint_sha256": anchor,
                "independent_worm_proven": False,
            }
        if seq < remote_seq:
            raise TraceDenied("receiver_checkpoint_local_rollback")
        if seq != remote_seq + 1:
            raise TraceDenied("receiver_checkpoint_multiple_unacknowledged")
        raw = _read(root / NAME, MAX_LEDGER)
        rows = raw.splitlines()
        if len(rows) != seq or len(rows[-1]) > MAX_RECEIPT:
            raise TraceDenied("receiver_checkpoint_ledger_changed")
        try:
            final = json.loads(rows[-1])
        except (ValueError, TypeError) as exc:
            raise TraceDenied("receiver_checkpoint_ledger_changed") from exc
        if (
            final.get("sequence") != remote_seq + 1
            or final.get("previous_receipt_sha256") != remote_head
            or receipt_digest(final) != head
        ):
            raise TraceDenied("receiver_checkpoint_conflicting_predecessor")
        return {
            "state": "PENDING_EXTERNAL_RECONCILIATION",
            "local_sequence": seq,
            "receipt_sha256": head,
            "previous_receipt_sha256": remote_head,
            "checkpoint_sha256": anchor,
            "independent_worm_proven": False,
            "custody_acknowledged": False,
        }
    finally:
        os.close(lock)
