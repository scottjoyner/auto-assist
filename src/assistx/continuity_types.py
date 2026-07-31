from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

SCHEMA_VERSION = 1
DURABILITY_LEVELS = {"ephemeral", "recoverable", "durable"}
SERVICE_STATUSES = {"healthy", "degraded", "draining", "offline"}
FINAL_TASK_STATES = {"completed", "failed", "cancelled"}
FENCE_PREFIXES = ("assistx-lease:", "witness:", "manual-break-glass:")
FORBIDDEN_CONTEXT_FIELDS = {
    "prompt",
    "messages",
    "token_ids",
    "raw_prompt",
    "raw_context",
}
MAX_EVENT_BYTES = 128 * 1024


class ContinuityError(RuntimeError):
    pass


class ContinuityConflict(ContinuityError):
    pass


class ContinuityRejected(ContinuityError):
    pass


@dataclass(frozen=True)
class ContinuityConfig:
    cluster_id: str
    node_id: str
    signing_secret: str
    event_stream_maxlen: int = 20_000
    heartbeat_ttl_ms: int = 45_000
    task_claim_ttl_ms: int = 120_000
    graph_name: str = "assistx_continuity"
    graph_projection_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.cluster_id.strip() or not self.node_id.strip():
            raise ValueError("cluster_id and node_id are required")
        if len(self.signing_secret) < 16:
            raise ValueError("signing_secret must contain at least 16 characters")
        if self.event_stream_maxlen < 100:
            raise ValueError("event_stream_maxlen must be at least 100")
        if self.heartbeat_ttl_ms < 5_000:
            raise ValueError("heartbeat_ttl_ms must be at least 5000")


def now_ms() -> int:
    return int(time.time() * 1000)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _text(value: Any, name: str, limit: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContinuityRejected(f"{name} is required")
    if len(text) > limit:
        raise ContinuityRejected(f"{name} exceeds {limit} characters")
    return text


def _object(
    value: Any,
    name: str,
    max_bytes: int = MAX_EVENT_BYTES,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContinuityRejected(f"{name} must be an object")
    result = dict(value)
    if len(canonical_json(result)) > max_bytes:
        raise ContinuityRejected(f"{name} exceeds {max_bytes} bytes")
    return result


def _fence(value: Any) -> str:
    proof = _text(value, "fence_proof", 256)
    if not proof.startswith(FENCE_PREFIXES):
        raise ContinuityRejected("fence_proof is not allowlisted")
    return proof


def event_signature(event: Mapping[str, Any], secret: str) -> str:
    unsigned = dict(event)
    unsigned.pop("signature", None)
    return hmac.new(
        secret.encode(),
        canonical_json(unsigned),
        hashlib.sha256,
    ).hexdigest()


def build_signed_event(
    *,
    cluster_id: str,
    source_node_id: str,
    epoch: int,
    kind: str,
    payload: Mapping[str, Any],
    durability: str,
    secret: str,
    idempotency_key: str | None = None,
    created_at_ms: int | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    if durability not in DURABILITY_LEVELS:
        raise ContinuityRejected(f"unsupported durability: {durability}")
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or f"cev-{uuid.uuid4().hex}",
        "cluster_id": _text(cluster_id, "cluster_id"),
        "source_node_id": _text(source_node_id, "source_node_id"),
        "epoch": max(0, int(epoch)),
        "kind": _text(kind, "kind", 120),
        "durability": durability,
        "idempotency_key": _text(
            idempotency_key or f"{kind}:{uuid.uuid4().hex}",
            "idempotency_key",
            256,
        ),
        "payload": _object(payload, "payload"),
        "created_at_ms": int(created_at_ms or now_ms()),
    }
    event["signature"] = event_signature(event, secret)
    return event


def verify_signed_event(
    event: Mapping[str, Any],
    config: ContinuityConfig,
) -> dict[str, Any]:
    candidate = dict(event)
    if candidate.get("schema_version") != SCHEMA_VERSION:
        raise ContinuityRejected("unsupported continuity event schema")
    if candidate.get("cluster_id") != config.cluster_id:
        raise ContinuityRejected("continuity event cluster mismatch")
    if candidate.get("durability") not in DURABILITY_LEVELS:
        raise ContinuityRejected("invalid continuity event durability")
    _text(candidate.get("event_id"), "event_id", 128)
    _text(candidate.get("source_node_id"), "source_node_id", 128)
    _text(candidate.get("kind"), "kind", 120)
    _text(candidate.get("idempotency_key"), "idempotency_key", 256)
    candidate["epoch"] = max(0, int(candidate.get("epoch") or 0))
    candidate["created_at_ms"] = int(candidate.get("created_at_ms") or 0)
    if candidate["created_at_ms"] <= 0:
        raise ContinuityRejected("invalid continuity event created_at_ms")
    candidate["payload"] = _object(candidate.get("payload"), "payload")
    supplied = str(candidate.get("signature") or "")
    expected = event_signature(candidate, config.signing_secret)
    if not hmac.compare_digest(supplied, expected):
        raise ContinuityRejected("continuity event signature mismatch")
    return candidate


class ContinuityStore(Protocol):
    config: ContinuityConfig

    def append_event(self, event: Mapping[str, Any]) -> dict[str, Any]: ...

    def current_epoch(self) -> int: ...

    def advance_epoch(self, epoch: int, fence_proof: str) -> dict[str, Any]: ...

    def record_heartbeat(
        self,
        heartbeat: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def list_services(
        self,
        *,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]: ...

    def acquire_role_lease(
        self,
        *,
        role: str,
        holder_node_id: str,
        epoch: int,
        ttl_ms: int,
        fence_proof: str,
    ) -> dict[str, Any]: ...

    def list_role_leases(self) -> list[dict[str, Any]]: ...

    def submit_task(self, task: Mapping[str, Any]) -> dict[str, Any]: ...

    def claim_task(
        self,
        *,
        node_id: str,
        capabilities: Iterable[str],
        epoch: int,
        ttl_ms: int | None = None,
    ) -> dict[str, Any] | None: ...

    def complete_task(
        self,
        *,
        task_id: str,
        node_id: str,
        claim_token: str,
        status: str,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def put_document(
        self,
        *,
        name: str,
        payload: Mapping[str, Any],
        epoch: int,
        ttl_ms: int,
    ) -> dict[str, Any]: ...

    def get_document(self, name: str) -> dict[str, Any] | None: ...

    def put_context_manifest(
        self,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def find_context_manifests(
        self,
        *,
        prefix_id: str,
        model_id: str,
        scope_id: str,
        compatibility_fingerprint: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    def pending_durable_events(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def mark_event_committed(
        self,
        event_id: str,
        *,
        committed_at_ms: int | None = None,
    ) -> None: ...

    def snapshot(self) -> dict[str, Any]: ...
