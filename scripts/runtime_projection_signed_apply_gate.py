"""Fail-closed two-signer source + operator gate for canonical fleet approval.

This module only verifies independent, separately trusted Ed25519 signatures.
It cannot make observations, issue signatures, authenticate users or perform
Neo4j writes. Signer key custody and issuing actual receipts are OUT OF SCOPE.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

AUDIENCE = "assistx-canonical-runtime-projection-approval-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
REVIEW_WINDOW_MS = 120_000
PHYSICAL_WINDOW_MS = 300_000
CANARY_WINDOW_MS = 180_000
CLOCK_SKEW_MS = 5_000


@dataclass(frozen=True)
class Proof:
    source_sha256: str
    source_observed_at_ms: int
    approved_at_ms: int
    approval_id: str
    generation: int


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def exact(value: object, keys: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(name + " keys invalid")
    return value


def integer(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError(name + " must be an integer")
    return value


def fresh(value: object, now_ms: int, max_age_ms: int, name: str) -> int:
    t = integer(value, name)
    if t > now_ms + CLOCK_SKEW_MS or now_ms - t > max_age_ms:
        raise ValueError(name + " stale or future-dated")
    return t


def verify_signature(wrapper: dict, key_bytes: bytes, name: str) -> dict:
    exact(wrapper, {"payload", "signature"}, name)
    payload = wrapper["payload"]
    if not isinstance(payload, dict) or len(canonical(payload)) > 48_000:
        raise ValueError(name + " payload invalid or too large")
    if (not isinstance(wrapper["signature"], str)
            or not re.fullmatch(r"[0-9a-f]{128}", wrapper["signature"])):
        raise ValueError(name + " signature encoding invalid")
    if type(key_bytes) is not bytes or len(key_bytes) != 32:
        raise ValueError(name + " requires a pinned Ed25519 public key")
    try:
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(
            bytes.fromhex(wrapper["signature"]), canonical(payload)
        )
    except (InvalidSignature, ValueError) as exc:
        raise ValueError(name + " signature not verified") from exc
    return payload



def require_uniqueness_constraints(records: object) -> None:
    """Reject apply unless both graph-wide replay and canonical CAS identity exist.

    Results must come from Neo4j's trusted SHOW CONSTRAINTS result, NEVER from
    the untrusted signed bundle or local config. This function writes nothing.
    """
    if not isinstance(records, list) or len(records) > 2048:
        raise ValueError("unavailable or unreasonable database constraint inventory")
    required = {
        ("RuntimeProjectionApprovalReceipt", "approval_id"),
        ("FleetProjectionState", "name"),
    }
    found: set[tuple[str, str]] = set()
    for row in records:
        exact(row, {"type", "labelsOrTypes", "properties"}, "constraint")
        typ = row["type"]
        if typ not in ("UNIQUENESS", "NODE_KEY"):
            continue
        labels, properties = row["labelsOrTypes"], row["properties"]
        if not isinstance(labels, list) or not isinstance(properties, list):
            raise ValueError("invalid graph constraint description")
        if len(labels) == 1 and len(properties) == 1:
            key = (labels[0], properties[0])
            if key in required:
                found.add(key)
    if found != required:
        raise ValueError("canonical and approval-replay uniqueness constraints required")


def validate(
    manifest: dict[str, Any], attestation: dict, source_key: bytes,
    operator_key: bytes, now_ms: int,
) -> Proof:
    """Only attested evidence may enter the write transaction.

    Manifest is the fully validated original schema-1 AssistX manifest.
    Source and operator keys must be trusted *outside this function*, held by
    distinct organizations/roles and delivered from fixed config, never from
    in-band JSON or a user-supplied free-text evidence_ref.
    """
    integer(now_ms, "now_ms")
    if source_key == operator_key:
        raise ValueError("source signer must be independent of operator signer")
    if len(canonical(attestation)) > 64_000:
        raise ValueError("attestation exceeds bounded size")
    exact(attestation, {"schema", "source", "operator"}, "attestation")
    if attestation["schema"] != "runtime-projection-two-signer-v1":
        raise ValueError("unknown attestation schema")
    src = verify_signature(attestation["source"], source_key, "source")
    op = verify_signature(attestation["operator"], operator_key, "operator")
    exact(src, {
        "audience", "role", "manifest_sha256", "generation",
        "approval_id", "observations", "issued_at_ms", "expires_at_ms",
    }, "signed source")
    exact(op, {
        "audience", "role", "manifest_sha256", "generation",
        "approval_id", "approved_by", "source_receipt_sha256",
        "approved_at_ms", "expires_at_ms",
    }, "signed operator")
    manifest_sha = hashlib.sha256(canonical(manifest)).hexdigest()
    generation = integer(manifest["generation"], "generation")
    for name, payload, role in (
        ("source", src, "source-observer"),
        ("operator", op, "operator-reviewer"),
    ):
        if (payload["audience"] != AUDIENCE or payload["role"] != role
                or payload["manifest_sha256"] != manifest_sha
                or payload["generation"] != generation
                or payload["approval_id"] != manifest["approval_id"]):
            raise ValueError(name + " identity and manifest binding mismatch")
    if (op["approved_by"] != manifest["approved_by"]
            or not isinstance(op["approved_by"], str)
            or len(op["approved_by"]) < 3):
        raise ValueError("reviewer identity mismatch")
    source_digest = hashlib.sha256(canonical(attestation["source"])).hexdigest()
    if op["source_receipt_sha256"] != source_digest:
        raise ValueError("reviewer did not bind exact source signature")
    source_time = fresh(src["issued_at_ms"], now_ms, PHYSICAL_WINDOW_MS,
                        "source issued")
    source_expiry = integer(src["expires_at_ms"], "source expires")
    if not source_time < source_expiry <= source_time + PHYSICAL_WINDOW_MS:
        raise ValueError("source approval interval invalid")
    if source_expiry <= now_ms:
        raise ValueError("source receipt expired")
    approved_at = fresh(op["approved_at_ms"], now_ms, REVIEW_WINDOW_MS,
                        "operator review")
    approval_expiry = integer(op["expires_at_ms"], "operator expires")
    if not approved_at < approval_expiry <= approved_at + REVIEW_WINDOW_MS:
        raise ValueError("operator approval interval invalid")
    if approval_expiry <= now_ms:
        raise ValueError("operator approval expired")
    if approved_at < source_time - CLOCK_SKEW_MS:
        raise ValueError("operator approval predates physical source evidence")
    observations = src["observations"]
    if not isinstance(observations, list) or len(observations) != len(manifest["runtimes"]):
        raise ValueError("signed runtime evidence count mismatch")
    by_runtime = {}
    for entry in observations:
        exact(entry, {
            "runtime_instance_id", "node_id", "process_id",
            "physical_observed_at_ms", "model_observed_at_ms",
            "capacity_observed_at_ms", "loaded_model_fingerprints",
            "max_safe_slots", "paths",
        }, "source runtime observation")
        rid = entry["runtime_instance_id"]
        if not isinstance(rid, str) or rid in by_runtime:
            raise ValueError("duplicate source runtime identity")
        by_runtime[rid] = entry
    for runtime in manifest["runtimes"]:
        rid = runtime["runtime_instance_id"]
        row = by_runtime.get(rid)
        if (row is None or row["node_id"] != runtime["node_id"]
                or str(row["process_id"]) != str(runtime["process_id"])):
            raise ValueError("runtime physical process identity mismatch")
        for field in ("physical_observed_at_ms", "model_observed_at_ms",
                      "capacity_observed_at_ms"):
            t = fresh(row[field], now_ms, PHYSICAL_WINDOW_MS, field)
            if t > source_time + CLOCK_SKEW_MS:
                raise ValueError("observation newer than signed source issue")
        slots = integer(row["max_safe_slots"], "source capacity")
        declared = integer(runtime["capacity"]["parallel_slots"], "manifest capacity")
        if slots < 1 or declared < 1 or declared > slots:
            raise ValueError("runtime capacity exceeds independently observed bound")
        artifacts = row["loaded_model_fingerprints"]
        expected = [m["artifact_fingerprint"] for m in runtime["models"]]
        if (not isinstance(artifacts, list) or
                len(artifacts) != len(set(artifacts)) or
                sorted(artifacts) != sorted(expected)):
            raise ValueError("loaded physical model artifact binding mismatch")
        paths = row["paths"]
        if not isinstance(paths, list) or len(paths) != len(runtime["access_paths"]):
            raise ValueError("source path count mismatch")
        by_transport = {}
        for entry in paths:
            exact(entry, {"transport", "url_sha256", "canary_observed_at_ms",
                          "completed", "http_status"}, "path source observation")
            transport = entry["transport"]
            if transport in by_transport:
                raise ValueError("duplicate source transport")
            by_transport[transport] = entry
        for path in runtime["access_paths"]:
            entry = by_transport.get(path["transport"])
            url_hash = hashlib.sha256(
                str(path["base_url"]).rstrip("/").encode()
            ).hexdigest()
            if (entry is None or entry["url_sha256"] != url_hash or
                    entry["completed"] is not True or
                    entry["http_status"] != 200):
                raise ValueError("direct path and canary source mismatch")
            t = fresh(entry["canary_observed_at_ms"], now_ms, CANARY_WINDOW_MS,
                      "direct path canary")
            if t > source_time + CLOCK_SKEW_MS:
                raise ValueError("direct canary newer than signed source issue")
    return Proof(
        source_sha256=source_digest,
        source_observed_at_ms=source_time,
        approved_at_ms=approved_at,
        approval_id=op["approval_id"],
        generation=generation,
    )
