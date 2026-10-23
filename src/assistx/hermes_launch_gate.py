"""Opt-in local owner-bound Hermes launch verification.

Validates the issued signed token with a pinned public key and requires
an exact live active claim before a child process can start. The fresh
heartbeat/lease check is enforced separately by strict_executor_adapter.
No unsigned claim metadata, task payload or environment header can assert
owner authority.
"""
from __future__ import annotations
import os
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .executor_security import ExecutorTokenCodec
from .owner_identity import canonical_identity

class LaunchDenied(ValueError):
    pass

def _trusted_public_key() -> Ed25519PublicKey:
    raw_path = os.getenv("ASSISTX_EXECUTOR_VERIFY_KEY_FILE", "")
    if (not raw_path or not os.path.isabs(raw_path)
            or os.path.realpath(raw_path) != raw_path
            or raw_path.startswith(("/workspace/", "/research/knowledge/"))):
        raise LaunchDenied("explicit immutable executor verify key required")
    cursor = Path("/")
    for name in Path(raw_path).parts[1:]:
        cursor = cursor / name
        if cursor.is_symlink():
            raise LaunchDenied("symlinked executor verify key")
    try:
        raw = Path(raw_path).read_bytes()
        if not 0 < len(raw) <= 4096:
            raise LaunchDenied("unbounded executor verification key")
        key = serialization.load_pem_public_key(raw)
        if not isinstance(key, Ed25519PublicKey):
            raise LaunchDenied("non-Ed25519 executor verification key")
        return key
    except (ValueError, OSError, TypeError) as exc:
        raise LaunchDenied("untrusted executor verification key") from exc

def verify_owner_bound_launch(client: Any, *, agent_id: str, model: str) -> dict:
    """Require a signed task token matching a currently active claim and owner.

    No caller-supplied owner fields are accepted; only the verified Ed25519
    token supplies authority. Must be followed by a fresh server heartbeat
    before launching the model subprocess.
    """
    try:
        if os.getenv("ASSISTX_REQUIRE_OWNER_BINDING", "").strip().lower() not in (
                "true", "1", "on", "yes"):
            raise LaunchDenied("server owner binding is not enabled")
        token = getattr(client, "task_token", "")
        if not isinstance(token, str) or not token:
            raise LaunchDenied("active signed executor token missing")
        jwt = ExecutorTokenCodec(public_key=_trusted_public_key()).decode(
            token, audience="assistx-executor")
        signed_owner = canonical_identity(jwt, origin="signed executor owner")
        active_claim = getattr(client, "task_claims", {})
        if not isinstance(active_claim, dict):
            raise LaunchDenied("active task claims not verified")
        if canonical_identity(active_claim, origin="current owner") != signed_owner:
            raise LaunchDenied("owner changed since token issuance")
        for field, active_value in (
            ("task_id", getattr(client, "_active_task_id", "")),
            ("claim_id", getattr(client, "_active_claim_id", "")),
            ("agent_id", agent_id),
        ):
            if (not isinstance(active_value, str) or not active_value
                    or jwt.get(field) != active_value
                    or active_claim.get(field) != active_value):
                raise LaunchDenied("active signed claim identity mismatch")
        generation = jwt.get("projection_generation")
        if (type(generation) is not int or generation <= 0
                or active_claim.get("projection_generation") != generation):
            raise LaunchDenied("projection generation changed or missing")
        scope = jwt.get("scopes")
        aliases = jwt.get("allowed_model_aliases")
        if (not isinstance(scope, list) or "inference" not in scope
                or not isinstance(aliases, list)
                or not isinstance(model, str) or not model
                or model not in aliases):
            raise LaunchDenied("inference or model is not authorized")
        if getattr(client, "_lease_lost", None) is None or client._lease_lost.is_set():
            raise LaunchDenied("executor lease is not active")
        return {
            "task_id": jwt["task_id"],
            "claim_id": jwt["claim_id"],
            "agent_id": jwt["agent_id"],
            "projection_generation": generation,
            "owner": signed_owner,
        }
    except LaunchDenied:
        raise
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise LaunchDenied("signed owner-bound executor launch denied") from exc

def verify_heartbeat_ack(client: Any, response: Any) -> None:
    """Fail closed on an HTTP-200 heartbeat with stale or contradictory Task.

    This is opt-in with the signed owner-bound launch gate. The API must
    independently authenticate each task request and revalidate the active
    claim; a worker cannot trust an HTTP status alone.
    """
    try:
        if type(response) is not dict or type(response.get("task")) is not dict:
            raise LaunchDenied("heartbeat omitted authoritative Task record")
        task = response["task"]
        token = getattr(client, "task_token", "")
        if not isinstance(token, str) or not token:
            raise LaunchDenied("heartbeat missing scoped executor token")
        signed = ExecutorTokenCodec(public_key=_trusted_public_key()).decode(
            token, audience="assistx-executor")
        if canonical_identity(task, origin="heartbeat Task owner") != canonical_identity(
                signed, origin="signed token owner"):
            raise LaunchDenied("heartbeat task owner changed")
        required = (
            ("id", signed.get("task_id"), getattr(client, "_active_task_id", "")),
            ("claim_id", signed.get("claim_id"), getattr(client, "_active_claim_id", "")),
            ("claimed_by", signed.get("agent_id"), client.task_claims.get("agent_id")),
            ("agent_session_id", getattr(client, "_active_session_id", ""),
             getattr(client, "_active_session_id", "")),
        )
        for field, from_token, expected in required:
            if (type(expected) is not str or not expected
                    or from_token != expected or task.get(field) != expected):
                raise LaunchDenied("heartbeat returned contradictory Task claim")
        if task.get("status") != "RUNNING":
            raise LaunchDenied("heartbeat Task not actively RUNNING")
        expiry = task.get("lease_expires_at_ts")
        token_expiry = signed.get("exp")
        if (type(expiry) is not int or type(token_expiry) is not int
                or expiry <= int(time.time() * 1000)
                or token_expiry * 1000 > expiry):
            raise LaunchDenied("heartbeat lease missing, expired, or shorter than token")
    except LaunchDenied:
        raise
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise LaunchDenied("heartbeat Task acknowledgement failed validation") from exc
