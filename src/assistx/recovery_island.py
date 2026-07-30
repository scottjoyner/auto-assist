from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

RECOVERY_ACTIVATION_VERSION = 1
_MAX_ACTIVATION_TTL_SECONDS = 3600
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_FENCE_PREFIXES = (
    "assistx-lease:",
    "manual-break-glass:",
    "witness:",
)


def _canonical_activation_payload(envelope: dict[str, Any]) -> bytes:
    payload = dict(envelope)
    attestation = dict(payload.get("attestation") or {})
    attestation.pop("signature", None)
    payload["attestation"] = attestation
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign_recovery_activation(
    activation: dict[str, Any],
    *,
    key_id: str,
    secret: str,
    ttl_seconds: int = 900,
    now: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Sign the distinct privilege required to activate a recovery island.

    Recovery runbooks and recovery activation use separate keys. A normal signed
    runbook may stage, inspect, and verify a recovery deployment, but it cannot
    advertise the deployment as active unless this envelope also verifies.
    """

    issued_at = int(now if now is not None else time.time())
    signed = dict(activation)
    signed.setdefault("version", RECOVERY_ACTIVATION_VERSION)
    signed.setdefault("mode", "activate")
    signed["attestation"] = {
        "algorithm": "hmac-sha256",
        "key_id": str(key_id),
        "issued_at": issued_at,
        "expires_at": issued_at
        + max(30, min(int(ttl_seconds), _MAX_ACTIVATION_TTL_SECONDS)),
        "nonce": nonce or hashlib.sha256(str(time.time_ns()).encode()).hexdigest(),
    }
    signed["attestation"]["signature"] = hmac.new(
        secret.encode("utf-8"),
        _canonical_activation_payload(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def verify_recovery_activation(
    envelope: dict[str, Any],
    keys: dict[str, str],
    *,
    node_id: str,
    deployment: str,
    bundle_sha256: str,
    minimum_epoch: int = 0,
    now: int | None = None,
) -> str | None:
    if not isinstance(envelope, dict):
        return "missing_recovery_activation"
    if envelope.get("version") != RECOVERY_ACTIVATION_VERSION:
        return "unsupported_recovery_activation_version"
    if envelope.get("mode") != "activate":
        return "recovery_activation_mode_mismatch"
    if str(envelope.get("target_node_id") or "") != node_id:
        return "recovery_activation_target_mismatch"
    if str(envelope.get("deployment") or "") != deployment:
        return "recovery_activation_deployment_mismatch"
    supplied_bundle = str(envelope.get("bundle_sha256") or "")
    if not _SHA256.fullmatch(supplied_bundle):
        return "invalid_recovery_bundle_sha256"
    if supplied_bundle != bundle_sha256:
        return "recovery_activation_bundle_mismatch"
    try:
        epoch = int(envelope.get("epoch") or 0)
    except (TypeError, ValueError):
        return "invalid_recovery_activation_epoch"
    if epoch <= int(minimum_epoch):
        return "stale_recovery_activation_epoch"
    fence_proof = str(envelope.get("fence_proof") or "")
    if not fence_proof.startswith(_ALLOWED_FENCE_PREFIXES):
        return "missing_recovery_fence_proof"

    attestation = envelope.get("attestation")
    if not isinstance(attestation, dict):
        return "missing_recovery_activation_attestation"
    if attestation.get("algorithm") != "hmac-sha256":
        return "unsupported_recovery_activation_algorithm"
    key_id = str(attestation.get("key_id") or "")
    secret = keys.get(key_id)
    if not secret:
        return "unknown_recovery_activation_key"
    current = int(now if now is not None else time.time())
    try:
        issued_at = int(attestation.get("issued_at") or 0)
        expires_at = int(attestation.get("expires_at") or 0)
    except (TypeError, ValueError):
        return "invalid_recovery_activation_window"
    if issued_at > current + 30:
        return "recovery_activation_issued_in_future"
    if (
        expires_at <= current
        or expires_at - issued_at < 30
        or expires_at - issued_at > _MAX_ACTIVATION_TTL_SECONDS
    ):
        return "recovery_activation_expired_or_invalid"
    nonce = str(attestation.get("nonce") or "")
    if len(nonce) < 32 or len(nonce) > 128:
        return "invalid_recovery_activation_nonce"
    supplied = str(attestation.get("signature") or "")
    expected = hmac.new(
        secret.encode("utf-8"),
        _canonical_activation_payload(envelope),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        return "recovery_activation_signature_mismatch"
    return None


def private_health_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower().strip("[]").rstrip(".")
    if host in {"localhost", "host.docker.internal", "gateway.docker.internal"}:
        return True
    if host.endswith((".lan", ".local", ".internal", ".ts.net")):
        return True
    if "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def json_mapping(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
