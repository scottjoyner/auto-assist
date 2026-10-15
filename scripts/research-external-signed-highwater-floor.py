#!/usr/bin/env python3
"""Disposable signed-external-floor readback. Never retries or authorizes release.

A caller-provided, freshly signed external HIGH-WATER FLOOR can expose local
anchor restores that look like unconsumed work. This does NOT ensure the
witness signer, root pin, freshness oracle, or witness history is independent
of the shared resource/anchor administrator. Read-only, import-only research.
"""
from __future__ import annotations

import base64
import binascii
import importlib.util
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_RESOURCE_ID = "auto-router-runtime-projection"
_KEY_ID = "external-monotonic-witness-research-v1"
_DOMAIN = b"assistx-external-highwater-floor-research/v1\x00"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_FIELDS = {
    "schema_version", "audience", "resource_id", "term", "commit_seq",
    "last_nonce", "receipt_digest", "key_epoch", "challenge",
    "issued_at_ms", "expires_at_ms",
}


@cache
def _classifier() -> Any:
    source = Path(__file__).with_name("research-terminal-receipt-readback.py")
    spec = importlib.util.spec_from_file_location("research_terminal_floor", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("research_only_terminal_readback_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _positive(value: Any) -> bool:
    return type(value) is int and 0 < value < 2**63


def canonical_witness(record: dict[str, Any]) -> bytes:
    """Unambiguous synthetic domain-separated signed checkpoint claim."""
    return _DOMAIN + json.dumps(
        record, sort_keys=True, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def _valid_witness(record: Any) -> bool:
    return (
        type(record) is dict and set(record) == _FIELDS
        and type(record["schema_version"]) is int and record["schema_version"] == 1
        and record["audience"] == "synthetic-independent-floor-only"
        and record["resource_id"] == _RESOURCE_ID
        and all(_positive(record[k]) for k in (
            "term", "commit_seq", "key_epoch", "issued_at_ms", "expires_at_ms",
        ))
        and all(isinstance(record[k], str) and _HASH.fullmatch(record[k]) for k in (
            "receipt_digest", "challenge",
        ))
        and isinstance(record["last_nonce"], str)
        and _NONCE.fullmatch(record["last_nonce"]) is not None
    )


def observe(
    resource: Path, anchor: Path, *, expected: Any,
    signed_floor: Any,
    pinned_external_root: Any,
    pinned_epoch: Any,
    challenge: Any,
    now_ms: Any,
) -> dict[str, Any]:
    """Signed floor first, then ordinary read-only source/anchor assessment.

    All verdicts remain HOLD or NO_GO; never expose a success gate. The root
    and challenge are independently SUPPLIED, not sourced from either DB.
    """
    result: dict[str, Any] = {
        "schema_version": 1,
        "release_authorized": False,
        "retry_authorized": False,
        "verdict": "UNKNOWN_NO_GO",
    }
    classifier = _classifier()
    if not classifier._valid_expected(expected):
        result["verdict"] = "EXPECTED_EVIDENCE_INVALID_NO_GO"
        return result
    if (
        not isinstance(pinned_external_root, bytes)
        or len(pinned_external_root) != 32
        or not _positive(pinned_epoch)
        or not isinstance(challenge, str)
        or _HASH.fullmatch(challenge) is None
        or not _positive(now_ms)
    ):
        result["verdict"] = "EXTERNAL_WITNESS_CUSTODY_OR_CHALLENGE_MISSING_NO_GO"
        return result
    if type(signed_floor) is not dict or set(signed_floor) != {
        "key_id", "record", "signature",
    } or signed_floor["key_id"] != _KEY_ID:
        result["verdict"] = "EXTERNAL_WITNESS_ENVELOPE_INVALID_NO_GO"
        return result
    record = signed_floor["record"]
    if not _valid_witness(record):
        result["verdict"] = "EXTERNAL_WITNESS_RECORD_INVALID_NO_GO"
        return result
    if (
        record["key_epoch"] != pinned_epoch
        or record["challenge"] != challenge
        or record["issued_at_ms"] > now_ms
        or now_ms >= record["expires_at_ms"]
        or record["expires_at_ms"] - record["issued_at_ms"] > 60_000
    ):
        result["verdict"] = "EXTERNAL_WITNESS_NOT_FRESH_OR_PINNED_NO_GO"
        return result
    sig_encoded = signed_floor["signature"]
    if not isinstance(sig_encoded, str) or len(sig_encoded) > 128:
        result["verdict"] = "EXTERNAL_WITNESS_SIGNATURE_INVALID_NO_GO"
        return result
    try:
        sig = base64.b64decode(sig_encoded, validate=True)
        if len(sig) != 64:
            raise ValueError("wrong signature length")
        Ed25519PublicKey.from_public_bytes(pinned_external_root).verify(
            sig, canonical_witness(record)
        )
    except (ValueError, TypeError, InvalidSignature, binascii.Error):
        result["verdict"] = "EXTERNAL_WITNESS_SIGNATURE_INVALID_NO_GO"
        return result
    if record["resource_id"] != expected["resource_id"] or record["term"] != expected["term"]:
        result["verdict"] = "EXTERNAL_WITNESS_RESOURCE_OR_TERM_CONFLICT_NO_GO"
        return result
    if record["commit_seq"] > expected["commit_seq"]:
        result["verdict"] = "EXTERNAL_WITNESS_AHEAD_OF_EXPECTATION_NO_GO"
        return result
    if record["commit_seq"] == expected["commit_seq"] and (
        record["last_nonce"] != expected["nonce"]
        or record["receipt_digest"] != expected["receipt_digest"]
    ):
        result["verdict"] = "EXTERNAL_WITNESS_RECEIPT_CONFLICT_NO_GO"
        return result
    if record["commit_seq"] < expected["previous_seq"] or (
        record["commit_seq"] == expected["previous_seq"]
        and record["last_nonce"] != expected["previous_nonce"]
    ):
        result["verdict"] = "EXTERNAL_WITNESS_UNRELATED_PRIOR_HISTORY_NO_GO"
        return result
    # Reuse the EXACT validated second local snapshot for both local
    # classification and floor comparison. A separate third read could
    # silently create a false joined result across changed DB generations.
    # These source and anchor snapshots still are NOT cross-DB atomic.
    if not classifier._safe_lab_paths(resource, anchor):
        result["verdict"] = "DISPOSABLE_SCOPE_INVALID_NO_GO"
        return result
    first = classifier._read_snapshot(resource, anchor)
    snapshot = classifier._read_snapshot(resource, anchor)
    if first is None or snapshot is None:
        result["verdict"] = "UNAVAILABLE_OR_UNTRUSTED_READBACK_NO_GO"
        return result
    if first != snapshot:
        result["verdict"] = "CHANGED_BETWEEN_READBACKS_NO_GO"
        return result
    local_verdict = classifier._classify(snapshot, expected)
    if local_verdict.endswith("NO_GO"):
        result["verdict"] = local_verdict
        return result
    if not snapshot[1][0] or len(snapshot[1][0]) != 1:
        result["verdict"] = "EXTERNAL_WITNESS_FINAL_READBACK_UNAVAILABLE_NO_GO"
        return result
    actual_term, actual_seq, actual_nonce = snapshot[1][0][0]
    if actual_term != record["term"]:
        result["verdict"] = "EXTERNAL_WITNESS_TERM_CONFLICT_NO_GO"
    elif actual_seq < record["commit_seq"]:
        result["verdict"] = "EXTERNAL_WITNESS_DETECTS_ANCHOR_RESTORE_NO_GO"
    elif actual_seq == record["commit_seq"] and actual_nonce != record["last_nonce"]:
        result["verdict"] = "EXTERNAL_WITNESS_SAME_SEQ_NONCE_CONFLICT_NO_GO"
    elif actual_seq > record["commit_seq"]:
        result["verdict"] = "EXTERNAL_WITNESS_BEHIND_CURRENT_ANCHOR_HOLD"
    else:
        result["verdict"] = local_verdict
    return result


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_EXTERNAL_AUTHORITY_OR_RELEASE_ENTRYPOINT")
