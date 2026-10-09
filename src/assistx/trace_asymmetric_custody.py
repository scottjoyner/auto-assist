"""Two-authority trace custody receipt: producer public key then receiver key.

No producer HMAC key is accepted by either receiver entry point.
Offline contract, not a WORM server, credential manager or execution gate.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .trace_execution_adapter import TraceDenied, TraceReceiptStore
from .trace_producer_manifest import manifest_digest, verify_and_restore_public
from .trace_segment_plan import canonical, digest

SCHEMA = "assistx.trace-external-witness.v2"
GENESIS = "0" * 64
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_.:@-]{1,120}\Z")
_SIG = re.compile(r"[A-Za-z0-9_-]{86}\Z")
_FIELDS = frozenset(
    {
        "schema",
        "algorithm",
        "witness_id",
        "producer_id",
        "producer_key_id",
        "producer_manifest_sha256",
        "node_id",
        "journal_sha256",
        "index_sha256",
        "records",
        "journal_last_hash",
        "sequence",
        "previous_receipt_sha256",
        "observed_at_ms",
    }
)


def _id(value: Any) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise TraceDenied("dual_custody_identity_invalid")


def _hex(value: Any) -> None:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise TraceDenied("dual_custody_digest_invalid")


def _pos(value: Any) -> None:
    if type(value) is not int or value <= 0:
        raise TraceDenied("dual_custody_integer_invalid")


def receipt_digest(receipt: dict[str, Any]) -> str:
    if not isinstance(receipt, dict) or set(receipt) != _FIELDS | {"signature"}:
        raise TraceDenied("dual_custody_schema_invalid")
    return digest(canonical(receipt))


def attest_with_producer_public_key(
    *,
    archive: Path,
    producer_manifest: dict[str, Any],
    producer_verifier: Ed25519PublicKey,
    decryption_passphrase: Path,
    producer_id: str,
    producer_key_id: str,
    node_id: str,
    expected_journal_sha256: str,
    expected_index_sha256: str,
    expected_manifest_generation: int,
    expected_previous_manifest_sha256: str,
    minimum_manifest_issued_at_ms: int,
    witness_signer: Ed25519PrivateKey,
    witness_id: str,
    receipt_sequence: int,
    previous_receipt_sha256: str,
    observed_at_ms: int,
) -> dict[str, Any]:
    """Decrypt and verify producer signature THEN separately sign receiver proof."""
    _id(witness_id)
    _pos(receipt_sequence)
    _pos(observed_at_ms)
    _hex(previous_receipt_sha256)
    if receipt_sequence == 1 and previous_receipt_sha256 != GENESIS:
        raise TraceDenied("dual_custody_genesis_invalid")
    if not isinstance(witness_signer, Ed25519PrivateKey):
        raise TraceDenied("dual_custody_signer_invalid")
    # No legacy HMAC key is even part of this API.
    raw = verify_and_restore_public(
        archive=archive,
        manifest=producer_manifest,
        producer_verifier=producer_verifier,
        passphrase=decryption_passphrase,
        producer_id=producer_id,
        key_id=producer_key_id,
        node_id=node_id,
        expected_journal_sha256=expected_journal_sha256,
        expected_index_sha256=expected_index_sha256,
        expected_generation=expected_manifest_generation,
        expected_previous_manifest_sha256=expected_previous_manifest_sha256,
        minimum_issued_at_ms=minimum_manifest_issued_at_ms,
    )
    rows = TraceReceiptStore._verify_data(raw)
    if not rows or any(row.get("node_id") != node_id for row in rows):
        raise TraceDenied("dual_custody_restored_identity_invalid")
    unsigned = {
        "schema": SCHEMA,
        "algorithm": "Ed25519",
        "witness_id": witness_id,
        "producer_id": producer_id,
        "producer_key_id": producer_key_id,
        "producer_manifest_sha256": manifest_digest(producer_manifest),
        "node_id": node_id,
        "journal_sha256": expected_journal_sha256,
        "index_sha256": expected_index_sha256,
        "records": len(rows),
        "journal_last_hash": rows[-1]["entry_hash"],
        "sequence": receipt_sequence,
        "previous_receipt_sha256": previous_receipt_sha256,
        "observed_at_ms": observed_at_ms,
    }
    signature = base64.urlsafe_b64encode(witness_signer.sign(canonical(unsigned))).decode("ascii").rstrip("=")
    return {**unsigned, "signature": signature}


def verify_dual_authority_receipt(
    receipt: dict[str, Any],
    *,
    witness_verifier: Ed25519PublicKey,
    witness_id: str,
    producer_id: str,
    producer_key_id: str,
    expected_producer_manifest_sha256: str,
    node_id: str,
    expected_journal_sha256: str,
    expected_index_sha256: str,
    expected_sequence: int,
    expected_previous_receipt_sha256: str,
    minimum_observed_at_ms: int,
) -> str:
    """Verify receiver signature + previously secured, independent head."""
    if not isinstance(receipt, dict) or set(receipt) != _FIELDS | {"signature"}:
        raise TraceDenied("dual_custody_schema_invalid")
    unsigned = {k: v for k, v in receipt.items() if k != "signature"}
    if unsigned["schema"] != SCHEMA or unsigned["algorithm"] != "Ed25519":
        raise TraceDenied("dual_custody_schema_invalid")
    for k in ("witness_id", "producer_id", "producer_key_id", "node_id"):
        _id(unsigned[k])
    for k in (
        "producer_manifest_sha256",
        "journal_sha256",
        "index_sha256",
        "journal_last_hash",
        "previous_receipt_sha256",
    ):
        _hex(unsigned[k])
    for k in ("records", "sequence", "observed_at_ms"):
        _pos(unsigned[k])
    _pos(expected_sequence)
    _hex(expected_previous_receipt_sha256)
    if type(minimum_observed_at_ms) is not int or minimum_observed_at_ms < 0:
        raise TraceDenied("dual_custody_minimum_timestamp_invalid")
    if any(
        (
            unsigned["witness_id"] != witness_id,
            unsigned["producer_id"] != producer_id,
            unsigned["producer_key_id"] != producer_key_id,
            unsigned["producer_manifest_sha256"] != expected_producer_manifest_sha256,
            unsigned["node_id"] != node_id,
            unsigned["journal_sha256"] != expected_journal_sha256,
            unsigned["index_sha256"] != expected_index_sha256,
        )
    ):
        raise TraceDenied("dual_custody_binding_mismatch")
    if (
        unsigned["sequence"] != expected_sequence
        or unsigned["previous_receipt_sha256"] != expected_previous_receipt_sha256
        or unsigned["observed_at_ms"] < minimum_observed_at_ms
    ):
        raise TraceDenied("dual_custody_replay_or_rollback")
    if expected_sequence == 1 and expected_previous_receipt_sha256 != GENESIS:
        raise TraceDenied("dual_custody_genesis_invalid")
    if not isinstance(witness_verifier, Ed25519PublicKey):
        raise TraceDenied("dual_custody_verifier_invalid")
    sig = receipt["signature"]
    if not isinstance(sig, str) or not _SIG.fullmatch(sig):
        raise TraceDenied("dual_custody_signature_invalid")
    try:
        witness_verifier.verify(base64.urlsafe_b64decode(sig + "=="), canonical(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise TraceDenied("dual_custody_signature_invalid") from exc
    return receipt_digest(receipt)
