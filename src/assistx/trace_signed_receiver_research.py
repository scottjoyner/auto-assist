"""Offline verification of SYNTHETIC receiver receipts; never authorizes effects.

No signer service, keys, receiver adapter, consensus, replay or takeover.
Cryptographic validity is only evidence that the holder of an externally
pinned key signed some bytes; it cannot establish key custody, completeness,
durability, or that a real effect sink used an atomic fencing transaction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from assistx.trace_effect_reconciliation_research import (
    Classification, Decision, Operation, ReceiverReceipt, reconcile,
)

SCHEMA = "assistx-synthetic-receiver-evidence-v1"
DOMAIN = b"assistx/synthetic/receiver-evidence/v1\x00"
RECEIPT_KEYS = frozenset(asdict(ReceiverReceipt(
    domain="x", operation_id="x", effect_id="x", owner="x", term=1,
    receipt_id="x", status="unknown", durable=False, boundary_term=None,
)).keys())
PAYLOAD_KEYS = RECEIPT_KEYS | {"schema", "receiver_id"}


@dataclass(frozen=True)
class SignedReceipt:
    key_id: str
    payload: bytes
    signature: bytes


def encode_for_fixture(receipt: ReceiverReceipt, receiver_id: str) -> bytes:
    """Canonical fixture bytes. NOT a production signing or receipt API."""
    record = {"schema": SCHEMA, "receiver_id": receiver_id, **asdict(receipt)}
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _unique_pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate-json-key")
        result[k] = v
    return result


def _decode_and_verify(
    signed: SignedReceipt, *, pinned_keys: Mapping[str, bytes],
    expected_receiver: str,
) -> ReceiverReceipt:
    if (not isinstance(signed, SignedReceipt)
        or type(signed.key_id) is not str
        or type(signed.payload) is not bytes
        or type(signed.signature) is not bytes
        or len(signed.payload) > 4096 or len(signed.payload) == 0
        or len(signed.signature) != 64):
        raise ValueError("untrusted-receipt-envelope")
    if signed.key_id not in pinned_keys:
        raise ValueError("unknown-receiver-public-key")
    key = pinned_keys[signed.key_id]
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("untrusted-public-key-material")
    # Key is from an OUT-OF-BAND pinned map, never from receipt payload.
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(
            signed.signature, DOMAIN + signed.payload)
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise ValueError("receiver-signature-invalid") from exc

    try:
        record = json.loads(signed.payload.decode("ascii"),
                            object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                ValueError("non-finite-json-value")))
        if (type(record) is not dict or set(record) != PAYLOAD_KEYS
            or record["schema"] != SCHEMA
            or type(record["receiver_id"]) is not str
            or record["receiver_id"] != expected_receiver
            or json.dumps(record, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
                != signed.payload):
            raise ValueError("noncanonical-or-mismatched-receiver")
        row = ReceiverReceipt(**{k: record[k] for k in RECEIPT_KEYS})
        return row
    except (TypeError, KeyError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid-signed-payload") from exc


def reconcile_signed(
    operation: Operation, envelopes: Sequence[SignedReceipt], *,
    pinned_keys: Mapping[str, bytes], expected_receiver: str,
    expected_key_id: str,
) -> Decision:
    """Observation only: every input must verify or the batch fails closed.

    A pinned signer alone cannot prove complete or durable effects. A
    missing receipt remains UNKNOWN, and even confirmed receiver testimony
    cannot authorize replay, takeover, capacity release or production work.
    """
    untrusted = lambda reason: Decision(Classification.UNTRUSTED, reason)
    if (type(expected_receiver) is not str or not expected_receiver
        or type(expected_key_id) is not str or not expected_key_id
        or expected_key_id not in pinned_keys
        or not isinstance(envelopes, (list, tuple)) or len(envelopes) > 128):
        return untrusted("invalid-out-of-band-receiver-pins")
    rows = []
    for envelope in envelopes:
        if not isinstance(envelope, SignedReceipt):
            return untrusted("malformed-signed-receipt")
        if envelope.key_id != expected_key_id:
            return untrusted("unexpected-receiver-key-id")
        try:
            rows.append(_decode_and_verify(envelope, pinned_keys=pinned_keys,
                                           expected_receiver=expected_receiver))
        except (ValueError, TypeError):
            return untrusted("receiver-evidence-verification-failed")
    return reconcile(operation, rows)
