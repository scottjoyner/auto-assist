"""Read-only AssistX claim attestation contract; NOT a command executor.

The issuer requires an existing claimed Neo4j Task and never claims/heartbeats
tasks or schedules a worker. A lease signature alone is INSUFFICIENT to execute:
nodes must additionally obtain a fresh, monotonic signed authority-state proof.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .trace_execution_adapter import TraceDenied

SCHEMA = "assistx.claim-lease-proof.v1"
ISSUER = "assistx-neo4j-claimed-task"
KEY_ID = "assistx-claim-lease-v1"
COMMAND = "probe.noop.v1"
FIELDS = frozenset(
    {
        "schema",
        "issuer",
        "key_id",
        "algorithm",
        "task_id",
        "claim_id",
        "node_id",
        "execution_attempt",
        "command_id",
        "issued_at_ms",
        "expires_at_ms",
        "claim_lease_expires_at_ms",
        "signature",
    }
)
IDENT = re.compile(r"^[A-Za-z0-9_.:@-]{1,160}$")
MAX_TTL_MS = 10_000
CLOCK_SKEW_MS = 1_000


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise TraceDenied("invalid_" + field)
    return value


def _require_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise TraceDenied("invalid_" + field)
    return value


def _payload(task: dict[str, Any]) -> dict[str, Any]:
    if "payload_json" in task and task["payload_json"] is not None:
        try:
            raw = json.loads(task["payload_json"])
        except (ValueError, TypeError) as exc:
            raise TraceDenied("invalid_claim_payload") from exc
    else:
        raw = task.get("payload")
    if not isinstance(raw, dict) or set(raw) != {"command_id"} or raw["command_id"] != COMMAND:
        raise TraceDenied("claim_not_synthetic_noop")
    return raw


def is_trace_probe_candidate(task: dict[str, Any] | None) -> bool:
    """Any trace-probe marker reserves the protected task path."""
    return isinstance(task, dict) and any(task.get(k) == "trace_probe" for k in ("ticket_type", "kind", "task_type"))


def is_trace_probe_task(task: dict[str, Any] | None) -> bool:
    """Use the canonical Neo4j upsert_ticket fields, never a lone task_type."""
    return (
        isinstance(task, dict)
        and task.get("ticket_type") == "trace_probe"
        and task.get("kind") == "trace_probe"
        and task.get("task_type", "trace_probe") == "trace_probe"
    )


def validate_claim(
    task: dict[str, Any] | None,
    *,
    task_id: str,
    claim_id: str,
    node_id: str,
    execution_attempt: int,
    now_ms: int,
) -> int:
    """Validate immutable identity, current claimed status, and live lease."""
    _require_id(task_id, "task_id")
    _require_id(claim_id, "claim_id")
    _require_id(node_id, "node_id")
    _require_int(execution_attempt, "execution_attempt")
    if not isinstance(task, dict) or not task:
        raise TraceDenied("claim_not_found")
    if str(task.get("id") or "") != task_id:
        raise TraceDenied("claim_task_mismatch")
    if not is_trace_probe_task(task):
        raise TraceDenied("claim_type_not_allowed")
    if task.get("status") not in {"CLAIMED", "RUNNING"}:
        raise TraceDenied("claim_inactive")
    if task.get("target_agent_id") != node_id or task.get("claimed_by") != node_id:
        raise TraceDenied("claim_wrong_node")
    if task.get("claim_id") != claim_id:
        raise TraceDenied("claim_superseded")
    if type(task.get("execution_attempt")) is not int or task["execution_attempt"] != execution_attempt:
        raise TraceDenied("claim_generation_mismatch")
    capabilities = task.get("required_capabilities")
    if not isinstance(capabilities, list) or "trace-probe" not in capabilities:
        raise TraceDenied("claim_missing_capability")
    expires = _require_int(task.get("lease_expires_at_ts"), "claim_lease_expires_at_ts")
    if expires <= now_ms + MAX_TTL_MS:
        raise TraceDenied("claim_lease_insufficient")
    _payload(task)
    return expires


def _read_key(path: Path, *, private: bool) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise TraceDenied("claim_signing_key_unavailable") from exc
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_nlink != 1
            or (st.st_mode & 0o022)
            or (private and st.st_mode & 0o077)
            or st.st_size > 16_384
        ):
            raise TraceDenied("claim_key_unsafe")
        return os.read(fd, st.st_size + 1)
    finally:
        os.close(fd)


def load_signer(path: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(_read_key(path, private=True), password=None)
    except (ValueError, TypeError) as exc:
        raise TraceDenied("invalid_claim_signer") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise TraceDenied("invalid_claim_signer")
    return key


def load_verifier(path: Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(_read_key(path, private=False))
    except (ValueError, TypeError) as exc:
        raise TraceDenied("invalid_claim_verifier") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise TraceDenied("invalid_claim_verifier")
    return key


def issue_lease_proof(
    task: dict[str, Any] | None,
    *,
    task_id: str,
    claim_id: str,
    node_id: str,
    execution_attempt: int,
    signer: Ed25519PrivateKey,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    expires = validate_claim(
        task,
        task_id=task_id,
        claim_id=claim_id,
        node_id=node_id,
        execution_attempt=execution_attempt,
        now_ms=now,
    )
    unsigned = {
        "schema": SCHEMA,
        "issuer": ISSUER,
        "algorithm": "Ed25519",
        "key_id": KEY_ID,
        "task_id": task_id,
        "claim_id": claim_id,
        "node_id": node_id,
        "execution_attempt": execution_attempt,
        "command_id": COMMAND,
        "issued_at_ms": now,
        "expires_at_ms": min(now + MAX_TTL_MS, expires),
        "claim_lease_expires_at_ms": expires,
    }
    signature = base64.urlsafe_b64encode(signer.sign(canonical(unsigned))).decode("ascii").rstrip("=")
    return {**unsigned, "signature": signature}


def verify_lease_signature(
    proof: dict[str, Any],
    *,
    node_id: str,
    verifier: Ed25519PublicKey,
    now_ms: int | None = None,
) -> str:
    """Cryptographic validity only; never use as execution authorization."""
    if not isinstance(proof, dict) or set(proof) != FIELDS:
        raise TraceDenied("invalid_lease_proof_schema")
    if (
        proof["schema"] != SCHEMA
        or proof["issuer"] != ISSUER
        or proof["algorithm"] != "Ed25519"
        or proof["key_id"] != KEY_ID
    ):
        raise TraceDenied("invalid_lease_proof_issuer")
    for key in ("node_id", "task_id", "claim_id"):
        _require_id(proof[key], key)
    if proof["node_id"] != node_id:
        raise TraceDenied("wrong_execution_node")
    if proof["command_id"] != COMMAND:
        raise TraceDenied("lease_command_not_allowed")
    _require_int(proof["execution_attempt"], "execution_attempt")
    issued = _require_int(proof["issued_at_ms"], "issued_at_ms")
    expiry = _require_int(proof["expires_at_ms"], "expires_at_ms")
    claim_expiry = _require_int(proof["claim_lease_expires_at_ms"], "claim_lease_expires_at_ms")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if expiry <= issued or expiry - issued > MAX_TTL_MS or expiry > claim_expiry:
        raise TraceDenied("invalid_lease_proof_ttl")
    if issued > now + CLOCK_SKEW_MS or expiry <= now:
        raise TraceDenied("lease_proof_expired_or_future")
    signature = proof["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature):
        raise TraceDenied("invalid_lease_signature")
    try:
        decoded = base64.urlsafe_b64decode(signature + "==")
        verifier.verify(decoded, canonical({k: v for k, v in proof.items() if k != "signature"}))
    except (ValueError, InvalidSignature) as exc:
        raise TraceDenied("lease_proof_signature_invalid") from exc
    return hashlib.sha256(canonical(proof)).hexdigest()


STATUS_SCHEMA = "assistx.claim-current-status.v1"
STATUS_TTL_MS = 1500
STATUS_FIELDS = frozenset(
    {
        "schema",
        "issuer",
        "key_id",
        "algorithm",
        "task_id",
        "claim_id",
        "node_id",
        "execution_attempt",
        "command_id",
        "lease_sha256",
        "challenge",
        "issued_at_ms",
        "expires_at_ms",
        "signature",
    }
)


def _challenge(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise TraceDenied("invalid_status_challenge")
    return value


def issue_current_status(
    task: dict[str, Any] | None,
    *,
    lease_proof: dict[str, Any],
    challenge: str,
    signer: Ed25519PrivateKey,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Second fresh Neo4j lookup required. Does not update any graph state."""
    _challenge(challenge)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    digest = verify_lease_signature(
        lease_proof,
        node_id=lease_proof.get("node_id", ""),
        verifier=signer.public_key(),
        now_ms=now,
    )
    validate_claim(
        task,
        task_id=lease_proof["task_id"],
        claim_id=lease_proof["claim_id"],
        node_id=lease_proof["node_id"],
        execution_attempt=lease_proof["execution_attempt"],
        now_ms=now,
    )
    unsigned = {
        "schema": STATUS_SCHEMA,
        "issuer": ISSUER,
        "key_id": KEY_ID,
        "algorithm": "Ed25519",
        "task_id": lease_proof["task_id"],
        "claim_id": lease_proof["claim_id"],
        "node_id": lease_proof["node_id"],
        "execution_attempt": lease_proof["execution_attempt"],
        "command_id": COMMAND,
        "lease_sha256": digest,
        "challenge": challenge,
        "issued_at_ms": now,
        "expires_at_ms": min(now + STATUS_TTL_MS, lease_proof["expires_at_ms"]),
    }
    signature = base64.urlsafe_b64encode(signer.sign(canonical(unsigned))).decode("ascii").rstrip("=")
    return {**unsigned, "signature": signature}


def verify_current_status(
    status: dict[str, Any],
    *,
    proof: dict[str, Any],
    challenge: str,
    verifier: Ed25519PublicKey,
    now_ms: int,
) -> None:
    _challenge(challenge)
    if not isinstance(status, dict) or set(status) != STATUS_FIELDS:
        raise TraceDenied("invalid_current_status_schema")
    if (
        status["schema"] != STATUS_SCHEMA
        or status["issuer"] != ISSUER
        or status["key_id"] != KEY_ID
        or status["algorithm"] != "Ed25519"
    ):
        raise TraceDenied("invalid_current_status_issuer")
    for k in ("node_id", "task_id", "claim_id", "execution_attempt", "command_id"):
        if status[k] != proof[k]:
            raise TraceDenied("current_status_lease_mismatch")
    if status["lease_sha256"] != hashlib.sha256(canonical(proof)).hexdigest():
        raise TraceDenied("current_status_digest_mismatch")
    if status["challenge"] != challenge:
        raise TraceDenied("current_status_challenge_mismatch")
    issued = _require_int(status["issued_at_ms"], "status_issued")
    expires = _require_int(status["expires_at_ms"], "status_expires")
    if (
        issued < proof["issued_at_ms"]
        or issued > now_ms + CLOCK_SKEW_MS
        or expires <= now_ms
        or expires <= issued
        or expires - issued > STATUS_TTL_MS
        or expires > proof["expires_at_ms"]
    ):
        raise TraceDenied("current_status_stale_or_invalid")
    signature = status["signature"]
    if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9_-]{86}", signature):
        raise TraceDenied("invalid_current_status_signature")
    try:
        verifier.verify(
            base64.urlsafe_b64decode(signature + "=="),
            canonical({k: v for k, v in status.items() if k != "signature"}),
        )
    except (ValueError, InvalidSignature) as exc:
        raise TraceDenied("current_status_signature_invalid") from exc


def verify_lease_proof(
    proof: dict[str, Any],
    *,
    node_id: str,
    verifier: Ed25519PublicKey,
    now_ms: int | None = None,
    current_status: dict[str, Any] | None = None,
    challenge: str | None = None,
) -> str:
    """Fail closed unless node has fresh signed challenge-bound Neo4j status."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    digest = verify_lease_signature(
        proof,
        node_id=node_id,
        verifier=verifier,
        now_ms=now,
    )
    if current_status is None or challenge is None:
        raise TraceDenied("current_authority_not_proven")
    verify_current_status(
        current_status,
        proof=proof,
        challenge=challenge,
        verifier=verifier,
        now_ms=now,
    )
    return digest
