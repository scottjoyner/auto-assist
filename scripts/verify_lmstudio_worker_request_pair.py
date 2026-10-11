"""Offline LM Studio gateway<->worker signed-request receipt PROTOCOL checker.

Only verifies the mathematical consistency of two Ed25519 signatures against
caller-supplied TEST/trust-registry public keys. Caller possession of any key
DOES NOT establish a real worker, operator, authenticated user or execution.
No network, model, signing-key generation, disk access, Neo4j or dispatcher.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

AUDIENCE = "assistx-lmstudio-request-worker-receipt-v1"
MAX_AGE_MS = 180_000
MAX_DURATION_MS = 90_000
SKEW_MS = 5_000
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_ID = re.compile(r"[a-zA-Z0-9_-]{16,96}\Z")
IDENTITY_FIELDS = {
    "boot_id", "worker_pid", "process_start_ticks", "model_sha256",
    "gateway_pid", "gateway_start_ticks", "worker_port", "gateway_port",
}
WORKER_FIELDS = {
    "audience", "role", "request_id", "owner_digest",
    "request_sha256", "nonce_sha256", "model_instance_id", "worker_identity",
    "started_ms", "completed_ms", "completion_state",
    "finish_reason", "output_sha256", "completion_tokens",
}
GATEWAY_FIELDS = {
    "audience", "role", "request_id", "owner_digest",
    "request_sha256", "nonce_sha256", "model_instance_id", "worker_identity",
    "started_ms", "completed_ms", "completion_state",
    "http_status", "response_id", "output_sha256", "worker_receipt_sha256",
}


@dataclass(frozen=True)
class Pair:
    request_id: str
    owner_digest: str
    receipt_sha256: str
    worker_boot_id: str
    worker_pid: int
    worker_start_ticks: int
    completed_ms: int


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(label + " missing or unexpected fields")
    return value


def lower_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ValueError(label + " invalid sha256")
    return value


def positive(value: Any, label: str, limit: int = 2**63 - 1) -> int:
    if type(value) is not int or not 1 <= value <= limit:
        raise ValueError(label + " invalid positive integer")
    return value


def verification_only(wrapper: Any, pinned_public_key: bytes, role: str) -> dict:
    record = exact(wrapper, {"payload", "signature"}, role + " wrapper")
    data = record["payload"]
    if not isinstance(data, dict) or len(canonical(data)) > 16_384:
        raise ValueError(role + " oversized or invalid payload")
    signature = record["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{128}", signature):
        raise ValueError(role + " malformed signature")
    if type(pinned_public_key) is not bytes or len(pinned_public_key) != 32:
        raise ValueError(role + " requires externally trusted raw public key")
    try:
        Ed25519PublicKey.from_public_bytes(pinned_public_key).verify(
            bytes.fromhex(signature), canonical(data),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ValueError(role + " invalid signature") from exc
    return data


def process_identity(data: Any) -> dict[str, Any]:
    row = exact(data, IDENTITY_FIELDS, "worker identity")
    boot = row["boot_id"]
    if not isinstance(boot, str):
        raise ValueError("boot ID must be canonical UUID")
    try:
        parsed = str(uuid.UUID(boot))
    except ValueError as exc:
        raise ValueError("boot ID must be canonical UUID") from exc
    if parsed != boot:
        raise ValueError("boot ID must be canonical lowercase")
    for name in (
        "worker_pid", "gateway_pid", "process_start_ticks", "gateway_start_ticks",
    ):
        positive(row[name], name)
    for name in ("worker_port", "gateway_port"):
        positive(row[name], name, 65535)
    if row["worker_pid"] == row["gateway_pid"] or (
        row["worker_port"] == row["gateway_port"]
    ):
        raise ValueError("gateway and worker cannot share physical identities")
    lower_hash(row["model_sha256"], "worker model")
    return row


def checked_interval(start: Any, end: Any, now_ms: int, label: str) -> tuple[int, int]:
    t0 = positive(start, label + " start")
    t1 = positive(end, label + " end")
    if t1 < t0 or t1 - t0 > MAX_DURATION_MS:
        raise ValueError(label + " invalid bounded completion interval")
    if t1 > now_ms + SKEW_MS or now_ms - t1 > MAX_AGE_MS:
        raise ValueError(label + " stale or future-dated")
    return t0, t1


def correlate(
    *, gateway_envelope: Any, worker_envelope: Any,
    trusted_gateway_public_key: bytes, trusted_worker_public_key: bytes,
    now_ms: int,
    already_seen_request_ids: frozenset[str] | None = None,
) -> Pair:
    """Fail closed on inconsistent/partial receipts; returns NOT authorization."""
    now = positive(now_ms, "current timestamp")
    if trusted_gateway_public_key == trusted_worker_public_key:
        raise ValueError("gateway and worker keys must be independently governed")
    worker = verification_only(
        worker_envelope, trusted_worker_public_key, "worker"
    )
    gateway = verification_only(
        gateway_envelope, trusted_gateway_public_key, "gateway"
    )
    exact(worker, WORKER_FIELDS, "worker payload")
    exact(gateway, GATEWAY_FIELDS, "gateway payload")
    for payload, role in ((worker, "worker"), (gateway, "gateway")):
        if payload["audience"] != AUDIENCE or payload["role"] != role:
            raise ValueError("wrong receipt audience or role")
        rid = payload["request_id"]
        if not isinstance(rid, str) or not REQUEST_ID.fullmatch(rid):
            raise ValueError("invalid opaque request ID")
        for name in ("owner_digest", "request_sha256", "nonce_sha256",
                     "output_sha256"):
            lower_hash(payload[name], name)
        if (not isinstance(payload["model_instance_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.:/@+-]{3,128}",
                                    payload["model_instance_id"])):
            raise ValueError("model instance ID invalid")
        process_identity(payload["worker_identity"])
    for name in ("request_id", "owner_digest", "request_sha256",
                 "nonce_sha256", "output_sha256", "model_instance_id",
                 "worker_identity"):
        if worker[name] != gateway[name]:
            raise ValueError("gateway and worker differ on " + name)
    if already_seen_request_ids is not None and (
        worker["request_id"] in already_seen_request_ids
    ):
        raise ValueError("request ID already witnessed")
    exact_receipt_hash = hashlib.sha256(canonical(worker_envelope)).hexdigest()
    if gateway["worker_receipt_sha256"] != exact_receipt_hash:
        raise ValueError("gateway did not countersign exact worker receipt")
    if worker["completion_state"] != "COMPLETED" or (
        gateway["completion_state"] != "COMPLETED"
    ):
        raise ValueError("incomplete or failed inference is not a completion receipt")
    if worker["finish_reason"] != "stop":
        raise ValueError("truncated or stopped worker stream is not completed")
    if positive(worker["completion_tokens"], "worker completion tokens") > 2048:
        raise ValueError("worker completion exceeds bounded receipt budget")
    if gateway["http_status"] != 200 or type(gateway["http_status"]) is not int:
        raise ValueError("gateway did not report HTTP 200")
    response_id = gateway["response_id"]
    if not isinstance(response_id, str) or not REQUEST_ID.fullmatch(response_id):
        raise ValueError("unusable gateway response ID")
    worker_start, worker_end = checked_interval(
        worker["started_ms"], worker["completed_ms"], now, "worker",
    )
    gateway_start, gateway_end = checked_interval(
        gateway["started_ms"], gateway["completed_ms"], now, "gateway",
    )
    if (gateway_start > worker_start + SKEW_MS
            or worker_end > gateway_end + SKEW_MS):
        raise ValueError("gateway/worker completion chronology inconsistent")
    ident = worker["worker_identity"]
    return Pair(
        request_id=worker["request_id"],
        owner_digest=worker["owner_digest"],
        receipt_sha256=exact_receipt_hash,
        worker_boot_id=ident["boot_id"],
        worker_pid=ident["worker_pid"],
        worker_start_ticks=ident["process_start_ticks"],
        completed_ms=worker_end,
    )


def review_only_result(pair: Pair) -> dict:
    """A signed mathematical pair is not proof of independent trust or a load."""
    return {
        "schema": "lmstudio-request-worker-review-only-v1",
        "status": "CRYPTOGRAPHIC_PAIR_CONSISTENT_ONLY",
        "request_id": pair.request_id,
        "source_receipt_sha256": pair.receipt_sha256,
        "worker_pid": pair.worker_pid,
        "worker_start_ticks": pair.worker_start_ticks,
        "process_boot_sha256_prefix": hashlib.sha256(
            pair.worker_boot_id.encode("utf-8")
        ).hexdigest()[:12],
        "completed_ms": pair.completed_ms,
        "producer_key_custody_established": False,
        "independent_worker_execution_attested": False,
        "trusted_owner_binding_established": False,
        "signed_operator_approval": False,
        "can_renew": False,
        "can_dispatch": False,
    }
