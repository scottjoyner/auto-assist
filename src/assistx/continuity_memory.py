from __future__ import annotations

import hashlib
import hmac
import threading
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from .continuity_types import (
    FINAL_TASK_STATES,
    FORBIDDEN_CONTEXT_FIELDS,
    SCHEMA_VERSION,
    SERVICE_STATUSES,
    ContinuityConfig,
    ContinuityConflict,
    ContinuityRejected,
    _fence,
    _object,
    _text,
    canonical_json,
    now_ms,
    verify_signed_event,
)


class InMemoryContinuityStore:
    """Reference state machine. Falkor/Redis uses the same validated records."""

    def __init__(self, config: ContinuityConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._epoch = 0
        self._epoch_proof = ""
        self._events: dict[str, dict[str, Any]] = {}
        self._event_ids: list[str] = []
        self._idempotency: dict[str, str] = {}
        self._services: dict[str, dict[str, Any]] = {}
        self._leases: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._task_idempotency: dict[str, str] = {}
        self._documents: dict[str, dict[str, Any]] = {}
        self._contexts: dict[str, dict[str, Any]] = {}

    def append_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        candidate = verify_signed_event(event, self.config)
        with self._lock:
            previous = self._idempotency.get(candidate["idempotency_key"])
            if previous:
                return {**self._events[previous], "idempotent_replay": True}
            if candidate["event_id"] in self._events:
                raise ContinuityConflict("continuity event_id already exists")
            record = {
                **candidate,
                "commit_state": (
                    "pending"
                    if candidate["durability"] == "durable"
                    else "not_required"
                ),
            }
            self._events[record["event_id"]] = record
            self._event_ids.append(record["event_id"])
            self._idempotency[record["idempotency_key"]] = record["event_id"]
            while len(self._event_ids) > self.config.event_stream_maxlen:
                expired_id = self._event_ids.pop(0)
                expired = self._events.get(expired_id)
                if expired and expired.get("commit_state") != "pending":
                    self._events.pop(expired_id, None)
            return dict(record)

    def current_epoch(self) -> int:
        return self._epoch

    def advance_epoch(self, epoch: int, fence_proof: str) -> dict[str, Any]:
        requested = int(epoch)
        proof = _fence(fence_proof)
        with self._lock:
            if requested <= self._epoch:
                raise ContinuityConflict("continuity epoch is stale")
            self._epoch = requested
            self._epoch_proof = proof
            self._leases.clear()
            return {
                "cluster_id": self.config.cluster_id,
                "epoch": requested,
                "fence_proof": proof,
                "updated_at_ms": now_ms(),
            }

    def record_heartbeat(
        self,
        heartbeat: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = now_ms()
        node_id = _text(heartbeat.get("node_id"), "node_id", 128)
        status = str(heartbeat.get("status") or "healthy")
        if status not in SERVICE_STATUSES:
            raise ContinuityRejected("unsupported service status")
        ttl = max(
            5_000,
            min(
                int(heartbeat.get("ttl_ms") or self.config.heartbeat_ttl_ms),
                300_000,
            ),
        )
        record = {
            "node_id": node_id,
            "hostname": str(heartbeat.get("hostname") or node_id),
            "status": status,
            "capabilities": sorted(
                {str(value) for value in heartbeat.get("capabilities") or []}
            ),
            "roles": sorted(
                {str(value) for value in heartbeat.get("roles") or []}
            ),
            "active_slots": max(0, int(heartbeat.get("active_slots") or 0)),
            "max_slots": max(0, int(heartbeat.get("max_slots") or 0)),
            "memory_total_mb": max(
                0,
                int(heartbeat.get("memory_total_mb") or 0),
            ),
            "memory_available_mb": max(
                0,
                int(heartbeat.get("memory_available_mb") or 0),
            ),
            "runtime_models": list(heartbeat.get("runtime_models") or [])[:50],
            "metadata": _object(
                heartbeat.get("metadata") or {},
                "metadata",
                32 * 1024,
            ),
            "observed_at_ms": int(
                heartbeat.get("observed_at_ms") or current
            ),
            "expires_at_ms": current + ttl,
        }
        with self._lock:
            self._services[node_id] = record
        return dict(record)

    def list_services(
        self,
        *,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        current = now_ms()
        with self._lock:
            values = []
            for record in self._services.values():
                item = dict(record)
                if item["expires_at_ms"] <= current:
                    if not include_expired:
                        continue
                    item["status"] = "offline"
                    item["expired"] = True
                values.append(item)
            return sorted(values, key=lambda item: item["node_id"])

    def acquire_role_lease(
        self,
        *,
        role: str,
        holder_node_id: str,
        epoch: int,
        ttl_ms: int,
        fence_proof: str,
    ) -> dict[str, Any]:
        role_name = _text(role, "role", 128)
        holder = _text(holder_node_id, "holder_node_id", 128)
        proof = _fence(fence_proof)
        current = now_ms()
        ttl = max(5_000, min(int(ttl_ms), 300_000))
        with self._lock:
            if int(epoch) != self._epoch:
                raise ContinuityConflict("role lease epoch is stale")
            existing = self._leases.get(role_name)
            if (
                existing
                and existing["expires_at_ms"] > current
                and existing["holder_node_id"] != holder
            ):
                raise ContinuityConflict("role lease is held by another node")
            nonce = uuid.uuid4().hex
            record = {
                "role": role_name,
                "holder_node_id": holder,
                "epoch": self._epoch,
                "fence_proof": proof,
                "nonce": nonce,
                "fence_token": hmac.new(
                    self.config.signing_secret.encode(),
                    canonical_json([role_name, holder, self._epoch, nonce]),
                    hashlib.sha256,
                ).hexdigest(),
                "acquired_at_ms": current,
                "expires_at_ms": current + ttl,
            }
            self._leases[role_name] = record
            return dict(record)

    def list_role_leases(self) -> list[dict[str, Any]]:
        current = now_ms()
        with self._lock:
            return sorted(
                (
                    dict(value)
                    for value in self._leases.values()
                    if value["expires_at_ms"] > current
                ),
                key=lambda item: item["role"],
            )

    def submit_task(self, task: Mapping[str, Any]) -> dict[str, Any]:
        current = now_ms()
        task_id = str(task.get("task_id") or f"ctask-{uuid.uuid4().hex}")
        idem = str(task.get("idempotency_key") or task_id)
        epoch = int(
            task.get("epoch")
            if task.get("epoch") is not None
            else self._epoch
        )
        if epoch != self._epoch:
            raise ContinuityConflict("task epoch is stale")
        with self._lock:
            previous = self._task_idempotency.get(idem)
            if previous:
                return {**self._tasks[previous], "idempotent_replay": True}
            record = {
                "task_id": _text(task_id, "task_id", 128),
                "idempotency_key": _text(idem, "idempotency_key", 256),
                "title": _text(task.get("title"), "title", 500),
                "kind": str(task.get("kind") or "continuity"),
                "epoch": epoch,
                "priority": max(
                    0,
                    min(int(task.get("priority") or 50), 100),
                ),
                "required_capabilities": sorted(
                    {
                        str(value)
                        for value in task.get("required_capabilities") or []
                    }
                ),
                "preferred_nodes": sorted(
                    {str(value) for value in task.get("preferred_nodes") or []}
                ),
                "payload": _object(task.get("payload") or {}, "payload"),
                "state": "queued",
                "created_at_ms": current,
                "expires_at_ms": current
                + max(
                    60_000,
                    min(
                        int(task.get("ttl_ms") or 86_400_000),
                        604_800_000,
                    ),
                ),
            }
            self._tasks[record["task_id"]] = record
            self._task_idempotency[idem] = record["task_id"]
            return dict(record)

    def claim_task(
        self,
        *,
        node_id: str,
        capabilities: Iterable[str],
        epoch: int,
        ttl_ms: int | None = None,
    ) -> dict[str, Any] | None:
        current = now_ms()
        node = _text(node_id, "node_id", 128)
        capset = {str(value) for value in capabilities}
        with self._lock:
            if int(epoch) != self._epoch:
                raise ContinuityConflict("task claim epoch is stale")
            candidates = sorted(
                (
                    value
                    for value in self._tasks.values()
                    if value["state"] == "queued"
                    and value["expires_at_ms"] > current
                    and set(value["required_capabilities"]).issubset(capset)
                ),
                key=lambda item: (
                    -item["priority"],
                    item["created_at_ms"],
                    item["task_id"],
                ),
            )
            if not candidates:
                return None
            task = candidates[0]
            token = uuid.uuid4().hex
            task.update(
                {
                    "state": "claimed",
                    "claimed_by": node,
                    "claim_token": token,
                    "claimed_at_ms": current,
                    "claim_expires_at_ms": current
                    + max(
                        10_000,
                        min(
                            int(ttl_ms or self.config.task_claim_ttl_ms),
                            900_000,
                        ),
                    ),
                }
            )
            return dict(task)

    def complete_task(
        self,
        *,
        task_id: str,
        node_id: str,
        claim_token: str,
        status: str,
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in FINAL_TASK_STATES:
            raise ContinuityRejected("unsupported final task status")
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ContinuityRejected("continuity task not found")
            if task.get("claimed_by") != node_id:
                raise ContinuityConflict("continuity task claimant mismatch")
            supplied = str(task.get("claim_token") or "")
            if not hmac.compare_digest(supplied, str(claim_token or "")):
                raise ContinuityConflict("continuity task claim token mismatch")
            if int(task.get("claim_expires_at_ms") or 0) <= now_ms():
                raise ContinuityConflict("continuity task claim expired")
            task.update(
                {
                    "state": status,
                    "result": _object(result or {}, "result"),
                    "completed_at_ms": now_ms(),
                }
            )
            return dict(task)

    def put_document(
        self,
        *,
        name: str,
        payload: Mapping[str, Any],
        epoch: int,
        ttl_ms: int,
    ) -> dict[str, Any]:
        if int(epoch) != self._epoch:
            raise ContinuityConflict("document epoch is stale")
        current = now_ms()
        record = {
            "name": _text(name, "name", 128),
            "epoch": self._epoch,
            "payload": _object(payload, "payload"),
            "created_at_ms": current,
            "expires_at_ms": current
            + max(10_000, min(int(ttl_ms), 86_400_000)),
        }
        with self._lock:
            self._documents[record["name"]] = record
        return dict(record)

    def get_document(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._documents.get(name)
            if (
                not record
                or record["expires_at_ms"] <= now_ms()
                or record["epoch"] != self._epoch
            ):
                return None
            return dict(record)

    def put_context_manifest(
        self,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        illegal = FORBIDDEN_CONTEXT_FIELDS.intersection(manifest)
        if illegal:
            raise ContinuityRejected(
                "raw prompt or token material is forbidden in continuity "
                "context manifests"
            )
        current = now_ms()
        record = {
            "cache_id": _text(
                manifest.get("cache_id") or f"kvc-{uuid.uuid4().hex}",
                "cache_id",
                128,
            ),
            "prefix_id": _text(manifest.get("prefix_id"), "prefix_id", 128),
            "model_id": _text(manifest.get("model_id"), "model_id", 300),
            "scope_id": _text(manifest.get("scope_id"), "scope_id", 300),
            "compatibility_fingerprint": _text(
                manifest.get("compatibility_fingerprint"),
                "compatibility_fingerprint",
                128,
            ),
            "node_id": _text(manifest.get("node_id"), "node_id", 128),
            "endpoint_id": _text(
                manifest.get("endpoint_id"),
                "endpoint_id",
                300,
            ),
            "runtime": _text(manifest.get("runtime"), "runtime", 80),
            "storage_tier": str(manifest.get("storage_tier") or "host"),
            "portable": bool(manifest.get("portable")),
            "token_count": max(0, int(manifest.get("token_count") or 0)),
            "bytes": max(0, int(manifest.get("bytes") or 0)),
            "artifact_ref": str(manifest.get("artifact_ref") or "") or None,
            "created_at_ms": int(manifest.get("created_at_ms") or current),
            "last_used_at_ms": current,
            "expires_at_ms": int(
                manifest.get("expires_at_ms") or current + 3_600_000
            ),
        }
        if record["expires_at_ms"] <= current:
            raise ContinuityRejected("context manifest is already expired")
        if len(canonical_json(record)) > 64 * 1024:
            raise ContinuityRejected("context manifest exceeds 65536 bytes")
        with self._lock:
            self._contexts[record["cache_id"]] = record
        return dict(record)

    def find_context_manifests(
        self,
        *,
        prefix_id: str,
        model_id: str,
        scope_id: str,
        compatibility_fingerprint: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        current = now_ms()
        with self._lock:
            values = [
                dict(value)
                for value in self._contexts.values()
                if value["expires_at_ms"] > current
                and value["prefix_id"] == prefix_id
                and value["model_id"] == model_id
                and value["scope_id"] == scope_id
                and (
                    not compatibility_fingerprint
                    or value["compatibility_fingerprint"]
                    == compatibility_fingerprint
                )
            ]
        values.sort(key=lambda item: item["last_used_at_ms"], reverse=True)
        return values[: max(1, min(int(limit), 100))]

    def pending_durable_events(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            values = [
                dict(self._events[event_id])
                for event_id in self._event_ids
                if self._events[event_id].get("commit_state") == "pending"
            ]
        return values[: max(1, min(int(limit), 1000))]

    def mark_event_committed(
        self,
        event_id: str,
        *,
        committed_at_ms: int | None = None,
    ) -> None:
        with self._lock:
            event = self._events.get(event_id)
            if not event:
                raise ContinuityRejected("continuity event not found")
            event["commit_state"] = "committed"
            event["committed_at_ms"] = int(committed_at_ms or now_ms())

    def snapshot(self) -> dict[str, Any]:
        current = now_ms()
        with self._lock:
            tasks = [
                dict(value)
                for value in self._tasks.values()
                if value["expires_at_ms"] > current
                and value["state"] in {"queued", "claimed"}
            ]
            documents = {
                key: dict(value)
                for key, value in self._documents.items()
                if value["expires_at_ms"] > current
                and value["epoch"] == self._epoch
            }
            return {
                "schema_version": SCHEMA_VERSION,
                "cluster_id": self.config.cluster_id,
                "node_id": self.config.node_id,
                "epoch": self._epoch,
                "epoch_fence_proof": self._epoch_proof,
                "generated_at_ms": current,
                "services": self.list_services(),
                "role_leases": self.list_role_leases(),
                "tasks": sorted(
                    tasks,
                    key=lambda item: (
                        -item["priority"],
                        item["created_at_ms"],
                    ),
                ),
                "documents": documents,
                "pending_durable_events": len(
                    self.pending_durable_events(limit=1000)
                ),
                "context_manifest_count": len(self._contexts),
            }
