"""Receiver-signed proof of independently restored encrypted trace custody.

Research-only verifier/receipt contract. The SIGNER private key belongs only
to the external witness custodian. This module does not persist the witness
head, run a server, publish to NAS, or grant execution/deployment authority.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .trace_execution_adapter import TraceDenied, TraceReceiptStore
from .trace_segment_bundle import _read, verify_bundle
from .trace_segment_plan import canonical, digest

SCHEMA = "assistx.trace-external-witness.v1"
GENESIS = "0" * 64
HEX64 = re.compile(r"[a-f0-9]{64}\Z")
IDENT = re.compile(r"[a-zA-Z0-9_.:@-]{1,120}\Z")
PAYLOAD_FIELDS = frozenset(
    {
        "schema",
        "witness_id",
        "algorithm",
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
SIGNED_FIELDS = PAYLOAD_FIELDS | {"signature"}


def _hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise TraceDenied("witness_invalid_" + label)
    return value


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise TraceDenied("witness_invalid_" + label)
    return value


def _positive(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise TraceDenied("witness_invalid_" + label)
    return value


def receipt_digest(receipt: dict[str, Any]) -> str:
    if not isinstance(receipt, dict) or set(receipt) != SIGNED_FIELDS:
        raise TraceDenied("witness_schema_invalid")
    return digest(canonical(receipt))


def attest_restored_archive(
    *,
    archive: Path,
    node_id: str,
    signing_key: bytes,
    passphrase: Path,
    witness_signer: Ed25519PrivateKey,
    witness_id: str,
    sequence: int,
    previous_receipt_sha256: str,
    observed_at_ms: int,
    expected_journal_sha256: str,
    expected_index_sha256: str,
) -> dict[str, Any]:
    """Only the receiver with decryption access and signer can emit a receipt.

    It MUST actually decrypt/restore all segments before issuing the signature.
    This is a fixture method until deployed on a separately controlled host.
    """
    _id(node_id, "node")
    _id(witness_id, "custodian")
    _positive(sequence, "sequence")
    _positive(observed_at_ms, "timestamp")
    _hex(previous_receipt_sha256, "previous_hash")
    journal_expected = _hex(expected_journal_sha256, "journal_digest")
    index_expected = _hex(expected_index_sha256, "index_digest")
    if sequence == 1 and previous_receipt_sha256 != GENESIS:
        raise TraceDenied("witness_genesis_invalid")
    if not isinstance(witness_signer, Ed25519PrivateKey):
        raise TraceDenied("witness_private_key_invalid")
    if archive.is_symlink() or archive.parent.is_symlink():
        raise TraceDenied("witness_archive_symlink")
    if archive.name != journal_expected or archive.parent.name != node_id:
        raise TraceDenied("witness_archive_namespace_mismatch")
    if digest(_read(archive / "index.json", 8 * 1024 * 1024)) != index_expected:
        raise TraceDenied("witness_remote_index_mismatch")
    raw = verify_bundle(
        archive,
        node_id=node_id,
        passphrase=passphrase,
        signing_key=signing_key,
    )
    if digest(raw) != journal_expected:
        raise TraceDenied("witness_restored_digest_mismatch")
    records = TraceReceiptStore._verify_data(raw)
    if not records or any(row.get("node_id") != node_id for row in records):
        raise TraceDenied("witness_restored_node_mismatch")
    unsigned = {
        "schema": SCHEMA,
        "witness_id": witness_id,
        "algorithm": "Ed25519",
        "node_id": node_id,
        "journal_sha256": journal_expected,
        "index_sha256": index_expected,
        "records": len(records),
        "journal_last_hash": records[-1]["entry_hash"],
        "sequence": sequence,
        "previous_receipt_sha256": previous_receipt_sha256,
        "observed_at_ms": observed_at_ms,
    }
    signature = base64.urlsafe_b64encode(witness_signer.sign(canonical(unsigned))).decode("ascii").rstrip("=")
    return {**unsigned, "signature": signature}


def verify_external_receipt(
    receipt: dict[str, Any],
    *,
    witness_verifier: Ed25519PublicKey,
    witness_id: str,
    node_id: str,
    journal_sha256: str,
    index_sha256: str,
    expected_sequence: int,
    expected_previous_receipt_sha256: str,
    minimum_observed_at_ms: int,
) -> str:
    """Verify trust-pinned external signature and exact anti-replay anchor.

    The caller MUST obtain expected_sequence/previous_hash from independently
    protected durable storage. Passing values from the receipt is insecure.
    """
    if not isinstance(receipt, dict) or set(receipt) != SIGNED_FIELDS:
        raise TraceDenied("witness_schema_invalid")
    unsigned = {key: value for key, value in receipt.items() if key != "signature"}
    if unsigned["schema"] != SCHEMA or unsigned["algorithm"] != "Ed25519":
        raise TraceDenied("witness_schema_invalid")
    _id(unsigned["witness_id"], "custodian")
    _id(unsigned["node_id"], "node")
    for name in ("journal_sha256", "index_sha256", "journal_last_hash", "previous_receipt_sha256"):
        _hex(unsigned[name], name)
    for name in ("sequence", "records", "observed_at_ms"):
        _positive(unsigned[name], name)
    _positive(expected_sequence, "expected_sequence")
    if type(minimum_observed_at_ms) is not int or minimum_observed_at_ms < 0:
        raise TraceDenied("witness_minimum_timestamp_invalid")
    if (
        unsigned["witness_id"] != witness_id
        or unsigned["node_id"] != node_id
        or unsigned["journal_sha256"] != journal_sha256
        or unsigned["index_sha256"] != index_sha256
    ):
        raise TraceDenied("witness_binding_mismatch")
    if (
        unsigned["sequence"] != expected_sequence
        or unsigned["previous_receipt_sha256"] != expected_previous_receipt_sha256
        or unsigned["observed_at_ms"] < minimum_observed_at_ms
    ):
        raise TraceDenied("witness_replay_or_rollback")
    if expected_sequence == 1 and expected_previous_receipt_sha256 != GENESIS:
        raise TraceDenied("witness_genesis_invalid")
    if not isinstance(witness_verifier, Ed25519PublicKey):
        raise TraceDenied("witness_public_key_invalid")
    signature = receipt["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature):
        raise TraceDenied("witness_signature_invalid")
    try:
        decoded = base64.urlsafe_b64decode(signature + "==")
        witness_verifier.verify(decoded, canonical(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise TraceDenied("witness_signature_invalid") from exc
    return receipt_digest(receipt)
