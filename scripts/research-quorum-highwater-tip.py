#!/usr/bin/env python3
"""Offline quorum signed hash-chain tip vs disposable terminal anchor.

No network / service / signing key / filesystem writes or execution endpoint.
All outputs HOLD/NO_GO with release and retry disallowed. A signature quorum
does not prove each signer has separate custody, the live latest tip, or an
actual resource COMMIT. This is a *negative research* verifier only.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.util
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_DOMAIN = b"assistx-quorum-highwater-tip-research/v1\x00"
_LINK_DOMAIN = b"assistx-highwater-link-research/v1\x00"
_RESOURCE = "auto-router-runtime-projection"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_TIP_FIELDS = {
    "resource_id", "term", "commit_seq", "last_nonce",
    "receipt_digest", "prev_hash", "head_hash",
}
_CERT_FIELDS = {
    "schema_version", "witness_id", "key_epoch", "challenge",
    "issued_at_ms", "expires_at_ms", "tip", "signature",
}
_BASE_FIELDS = {
    "resource_id", "term", "commit_seq", "last_nonce",
    "receipt_digest", "head_hash",
}


def _positive(x: Any) -> bool:
    return type(x) is int and 0 < x < 2**63


def _sha(x: Any) -> bool:
    return isinstance(x, str) and _SHA.fullmatch(x) is not None


def canonical(value: dict[str, Any]) -> bytes:
    return _DOMAIN + json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("ascii")


def link(tip: dict[str, Any]) -> str:
    fields = {k: v for k, v in tip.items() if k != "head_hash"}
    body = json.dumps(
        fields, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(_LINK_DOMAIN + body).hexdigest()


def valid_tip(tip: Any) -> bool:
    return (
        type(tip) is dict and set(tip) == _TIP_FIELDS
        and tip["resource_id"] == _RESOURCE
        and _positive(tip["term"]) and _positive(tip["commit_seq"])
        and isinstance(tip["last_nonce"], str)
        and _NONCE.fullmatch(tip["last_nonce"]) is not None
        and all(_sha(tip[k]) for k in ("receipt_digest", "prev_hash", "head_hash"))
        and link(tip) == tip["head_hash"]
    )


def valid_baseline(record: Any) -> bool:
    return (
        type(record) is dict and set(record) == _BASE_FIELDS
        and record["resource_id"] == _RESOURCE
        and _positive(record["term"]) and _positive(record["commit_seq"])
        and isinstance(record["last_nonce"], str)
        and _NONCE.fullmatch(record["last_nonce"]) is not None
        and all(_sha(record[k]) for k in ("receipt_digest", "head_hash"))
    )


@cache
def _local():
    file = Path(__file__).with_name("research-terminal-receipt-readback.py")
    spec = importlib.util.spec_from_file_location("quorum_research_local", file)
    if spec is None or spec.loader is None:
        raise RuntimeError("research_local_verifier_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assess(
    resource: Path, anchor: Path, *, expected: Any,
    signed_heads: Any, pinned_roots: Any,
    baseline: Any, fresh_challenge: Any, now_ms: Any,
    required_quorum: int = 2,
) -> dict[str, Any]:
    """Read-only quorum and local consistency; NEVER return a release GO."""
    result = {
        "schema_version": 1, "release_authorized": False,
        "retry_authorized": False, "verdict": "UNKNOWN_NO_GO",
    }

    def deny(reason: str):
        result["verdict"] = reason
        return result

    local = _local()
    if not local._valid_expected(expected):
        return deny("EXPECTED_EVIDENCE_INVALID_NO_GO")
    if not valid_baseline(baseline):
        return deny("PINNED_HISTORY_BASELINE_INVALID_NO_GO")
    if (
        baseline["resource_id"] != expected["resource_id"]
        or baseline["term"] != expected["term"]
        or baseline["commit_seq"] > expected["commit_seq"]
    ):
        return deny("PINNED_HISTORY_OUTSIDE_EXPECTED_TERM_NO_GO")
    if (
        type(pinned_roots) is not dict or len(pinned_roots) != 3
        or any(not isinstance(i, str) or not i for i in pinned_roots)
        or any(
            type(v) is not dict or set(v) != {"public", "epoch"}
            or not isinstance(v["public"], bytes) or len(v["public"]) != 32
            or not _positive(v["epoch"]) for v in pinned_roots.values()
        )
        or type(required_quorum) is not int or required_quorum != 2
        or not _sha(fresh_challenge) or not _positive(now_ms)
    ):
        return deny("QUORUM_CUSTODY_OR_CHALLENGE_MISSING_NO_GO")
    if type(signed_heads) is not list or not 2 <= len(signed_heads) <= 3:
        return deny("WITNESS_QUORUM_UNAVAILABLE_NO_GO")
    seen: set[str] = set()
    tips: list[dict[str, Any]] = []
    for cert in signed_heads:
        if (
            type(cert) is not dict or set(cert) != _CERT_FIELDS
            or cert["schema_version"] != 1
            or type(cert["schema_version"]) is not int
            or type(cert["witness_id"]) is not str
            or cert["witness_id"] not in pinned_roots
        ):
            return deny("WITNESS_CERTIFICATE_MALFORMED_NO_GO")
        wid = cert["witness_id"]
        if wid in seen:
            return deny("DUPLICATE_WITNESS_IDENTITY_NO_GO")
        seen.add(wid)
        if (
            not _positive(cert["key_epoch"])
            or cert["key_epoch"] != pinned_roots[wid]["epoch"]
            or cert["challenge"] != fresh_challenge
            or not _positive(cert["issued_at_ms"])
            or not _positive(cert["expires_at_ms"])
            or cert["issued_at_ms"] > now_ms
            or now_ms >= cert["expires_at_ms"]
            or cert["expires_at_ms"] - cert["issued_at_ms"] > 60_000
        ):
            return deny("WITNESS_CERTIFICATE_NOT_FRESH_NO_GO")
        if not valid_tip(cert["tip"]):
            return deny("WITNESS_HASH_CHAIN_TIP_INVALID_NO_GO")
        encoded = cert["signature"]
        if not isinstance(encoded, str) or len(encoded) > 128:
            return deny("WITNESS_SIGNATURE_INVALID_NO_GO")
        try:
            signature = base64.b64decode(encoded, validate=True)
            if len(signature) != 64:
                raise ValueError("invalid signature length")
            signed_part = {k: v for k, v in cert.items() if k != "signature"}
            Ed25519PublicKey.from_public_bytes(
                pinned_roots[wid]["public"]
            ).verify(signature, canonical(signed_part))
        except (ValueError, TypeError, binascii.Error, InvalidSignature):
            return deny("WITNESS_SIGNATURE_INVALID_NO_GO")
        tips.append(cert["tip"])
    if len(tips) < required_quorum:
        return deny("WITNESS_QUORUM_UNAVAILABLE_NO_GO")
    # Reject ANY conflicting authenticated tip, even a single differing one.
    # This conservative research policy prefers availability loss to a fork.
    if any(x != tips[0] for x in tips[1:]):
        return deny("WITNESS_TIP_FORK_OR_STALE_CONFLICT_NO_GO")
    tip = tips[0]
    if tip["term"] != baseline["term"] or tip["resource_id"] != baseline["resource_id"]:
        return deny("WITNESS_TERM_CONFLICT_NO_GO")
    if tip["commit_seq"] == baseline["commit_seq"]:
        if (
            tip["last_nonce"] != baseline["last_nonce"]
            or tip["receipt_digest"] != baseline["receipt_digest"]
            or tip["head_hash"] != baseline["head_hash"]
        ):
            return deny("WITNESS_EQUAL_SEQ_FORK_NO_GO")
    elif tip["commit_seq"] == baseline["commit_seq"] + 1:
        if tip["prev_hash"] != baseline["head_hash"]:
            return deny("WITNESS_HASH_CHAIN_DISCONTINUITY_NO_GO")
    else:
        return deny("WITNESS_OLD_OR_NONADJACENT_HEAD_NO_GO")
    if tip["commit_seq"] > expected["commit_seq"]:
        return deny("WITNESS_AHEAD_OF_EXPECTED_EFFECT_NO_GO")
    if tip["commit_seq"] == expected["commit_seq"] and (
        tip["last_nonce"] != expected["nonce"]
        or tip["receipt_digest"] != expected["receipt_digest"]
    ):
        return deny("WITNESS_EFFECT_DIGEST_CONFLICT_NO_GO")
    if not local._safe_lab_paths(resource, anchor):
        return deny("DISPOSABLE_SCOPE_INVALID_NO_GO")
    first = local._read_snapshot(resource, anchor)
    second = local._read_snapshot(resource, anchor)
    if first is None or second is None:
        return deny("RESOURCE_OR_ANCHOR_READBACK_UNAVAILABLE_NO_GO")
    if first != second:
        return deny("CHANGED_BETWEEN_READBACKS_NO_GO")
    verdict = local._classify(second, expected)
    if verdict.endswith("NO_GO"):
        return deny(verdict)
    if len(second[1][0]) != 1:
        return deny("ANCHOR_READBACK_INVALID_NO_GO")
    term, seq, nonce = second[1][0][0]
    if term != tip["term"]:
        return deny("ANCHOR_VS_WITNESS_TERM_CONFLICT_NO_GO")
    if seq < tip["commit_seq"]:
        return deny("QUORUM_DETECTS_LOCAL_ANCHOR_RESTORE_NO_GO")
    if seq == tip["commit_seq"] and nonce != tip["last_nonce"]:
        return deny("ANCHOR_VS_WITNESS_NONCE_CONFLICT_NO_GO")
    if seq > tip["commit_seq"]:
        result["verdict"] = "QUORUM_TIP_BEHIND_ANCHOR_HOLD"
    else:
        result["verdict"] = verdict
    return result


if __name__ == "__main__":
    raise SystemExit("RESEARCH_ONLY_NO_QUORUM_AUTHORITY_OR_RELEASE_ENTRYPOINT")
