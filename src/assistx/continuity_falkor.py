from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

try:
    from redis import Redis
    from redis.exceptions import WatchError
except Exception:  # pragma: no cover
    Redis = Any  # type: ignore[misc,assignment]

    class WatchError(Exception):
        pass

from .continuity_memory import InMemoryContinuityStore
from .continuity_types import (
    FINAL_TASK_STATES,
    SCHEMA_VERSION,
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


class FalkorContinuityStore:
    """Redis/FalkorDB store with Redis primitives as the atomic hot ledger.

    Falkor graph projections are secondary. A graph query failure cannot
    invalidate leases, task claims, epochs, or the durable outbox.
    """

    def __init__(
        self,
        config: ContinuityConfig,
        url: str,
        client: Redis | None = None,
    ) -> None:
        self.config = config
        self.client = client or Redis.from_url(
            url,
            decode_responses=True,
            socket_timeout=3,
            health_check_interval=15,
        )
        self.prefix = f"assistx:continuity:{config.cluster_id}"

    @property
    def epoch_key(self) -> str:
        return f"{self.prefix}:epoch"

    @property
    def proof_key(self) -> str:
        return f"{self.prefix}:epoch-proof"

    @property
    def events_key(self) -> str:
        return f"{self.prefix}:events"

    @property
    def event_index_key(self) -> str:
        return f"{self.prefix}:event-index"

    @property
    def pending_key(self) -> str:
        return f"{self.prefix}:pending-durable"

    @property
    def services_key(self) -> str:
        return f"{self.prefix}:services"

    @property
    def leases_key(self) -> str:
        return f"{self.prefix}:leases"

    @property
    def tasks_key(self) -> str:
        return f"{self.prefix}:tasks"

    @property
    def task_queue_key(self) -> str:
        return f"{self.prefix}:task-queue"

    @property
    def documents_key(self) -> str:
        return f"{self.prefix}:documents"

    @property
    def contexts_key(self) -> str:
        return f"{self.prefix}:contexts"

    def ping(self) -> bool:
        return bool(self.client.ping())

    def memory_info(self) -> dict[str, Any]:
        info = self.client.info("memory")
        return {
            key: info.get(key)
            for key in (
                "used_memory",
                "used_memory_human",
                "maxmemory",
                "maxmemory_human",
                "maxmemory_policy",
            )
        }

    def current_epoch(self) -> int:
        return int(self.client.get(self.epoch_key) or 0)

    def append_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        candidate = verify_signed_event(event, self.config)
        idem_digest = hashlib.sha256(
            candidate["idempotency_key"].encode()
        ).hexdigest()
        idem_key = f"{self.prefix}:idem:{idem_digest}"
        event_key = f"{self.prefix}:event:{candidate['event_id']}"
        record = {
            **candidate,
            "commit_state": (
                "pending"
                if candidate["durability"] == "durable"
                else "not_required"
            ),
        }
        for _ in range(8):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(idem_key, event_key)
                    existing = pipe.get(idem_key)
                    if existing:
                        raw = self.client.get(
                            f"{self.prefix}:event:{existing}"
                        )
                        if raw:
                            return {
                                **json.loads(raw),
                                "idempotent_replay": True,
                            }
                        return record
                    if pipe.exists(event_key):
                        raise ContinuityConflict(
                            "continuity event_id already exists"
                        )
                    encoded = json.dumps(record, separators=(",", ":"))
                    pipe.multi()
                    pipe.set(event_key, encoded, ex=604_800)
                    pipe.set(
                        idem_key,
                        candidate["event_id"],
                        ex=604_800,
                    )
                    pipe.xadd(
                        self.events_key,
                        {
                            "event_id": candidate["event_id"],
                            "record": encoded,
                        },
                        maxlen=self.config.event_stream_maxlen,
                        approximate=True,
                    )
                    pipe.zadd(
                        self.event_index_key,
                        {
                            candidate["event_id"]: candidate[
                                "created_at_ms"
                            ]
                        },
                    )
                    if candidate["durability"] == "durable":
                        pipe.zadd(
                            self.pending_key,
                            {
                                candidate["event_id"]: candidate[
                                    "created_at_ms"
                                ]
                            },
                        )
                    pipe.execute()
                    return record
                except WatchError:
                    continue
        raise ContinuityConflict("continuity event append was contended")

    def advance_epoch(self, epoch: int, fence_proof: str) -> dict[str, Any]:
        requested = int(epoch)
        proof = _fence(fence_proof)
        for _ in range(8):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(self.epoch_key)
                    current = int(pipe.get(self.epoch_key) or 0)
                    if requested <= current:
                        raise ContinuityConflict("continuity epoch is stale")
                    pipe.multi()
                    pipe.set(self.epoch_key, requested)
                    pipe.set(self.proof_key, proof)
                    pipe.delete(self.leases_key)
                    pipe.execute()
                    return {
                        "cluster_id": self.config.cluster_id,
                        "epoch": requested,
                        "fence_proof": proof,
                        "updated_at_ms": now_ms(),
                    }
                except WatchError:
                    continue
        raise ContinuityConflict("continuity epoch update was contended")

    def _reference(self) -> InMemoryContinuityStore:
        reference = InMemoryContinuityStore(self.config)
        reference._epoch = self.current_epoch()
        return reference

    def record_heartbeat(
        self,
        heartbeat: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = self._reference().record_heartbeat(heartbeat)
        self.client.hset(
            self.services_key,
            record["node_id"],
            json.dumps(record, separators=(",", ":")),
        )
        self._project(
            "Service",
            "id",
            record["node_id"],
            {
                "status": record["status"],
                "expires_at_ms": record["expires_at_ms"],
            },
        )
        return record

    def list_services(
        self,
        *,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        current = now_ms()
        values = []
        for raw in self.client.hvals(self.services_key):
            record = json.loads(raw)
            if record["expires_at_ms"] <= current:
                if not include_expired:
                    continue
                record["status"] = "offline"
                record["expired"] = True
            values.append(record)
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
        ttl = max(5_000, min(int(ttl_ms), 300_000))
        for _ in range(8):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(self.epoch_key, self.leases_key)
                    current_epoch = int(pipe.get(self.epoch_key) or 0)
                    if int(epoch) != current_epoch:
                        raise ContinuityConflict("role lease epoch is stale")
                    raw = pipe.hget(self.leases_key, role_name)
                    existing = json.loads(raw) if raw else None
                    current = now_ms()
                    if (
                        existing
                        and existing["expires_at_ms"] > current
                        and existing["holder_node_id"] != holder
                    ):
                        raise ContinuityConflict(
                            "role lease is held by another node"
                        )
                    nonce = uuid.uuid4().hex
                    record = {
                        "role": role_name,
                        "holder_node_id": holder,
                        "epoch": current_epoch,
                        "fence_proof": proof,
                        "nonce": nonce,
                        "fence_token": hmac.new(
                            self.config.signing_secret.encode(),
                            canonical_json(
                                [role_name, holder, current_epoch, nonce]
                            ),
                            hashlib.sha256,
                        ).hexdigest(),
                        "acquired_at_ms": current,
                        "expires_at_ms": current + ttl,
                    }
                    pipe.multi()
                    pipe.hset(
                        self.leases_key,
                        role_name,
                        json.dumps(record, separators=(",", ":")),
                    )
                    pipe.execute()
                    self._project(
                        "Role",
                        "name",
                        role_name,
                        {
                            "holder_node_id": holder,
                            "epoch": current_epoch,
                            "expires_at_ms": record["expires_at_ms"],
                        },
                    )
                    return record
                except WatchError:
                    continue
        raise ContinuityConflict("role lease acquisition was contended")

    def list_role_leases(self) -> list[dict[str, Any]]:
        current = now_ms()
        values = []
        for raw in self.client.hvals(self.leases_key):
            record = json.loads(raw)
            if record["expires_at_ms"] > current:
                values.append(record)
        return sorted(values, key=lambda item: item["role"])

    def submit_task(self, task: Mapping[str, Any]) -> dict[str, Any]:
        record = self._reference().submit_task(task)
        digest = hashlib.sha256(
            record["idempotency_key"].encode()
        ).hexdigest()
        idem = f"{self.prefix}:task-idem:{digest}"
        existing = self.client.get(idem)
        if existing:
            raw = self.client.hget(self.tasks_key, existing)
            if raw:
                return {**json.loads(raw), "idempotent_replay": True}
            return record
        with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(
                self.tasks_key,
                record["task_id"],
                json.dumps(record, separators=(",", ":")),
            )
            pipe.set(idem, record["task_id"], ex=604_800)
            score = (
                (100 - record["priority"]) * 10**13
                + record["created_at_ms"]
            )
            pipe.zadd(self.task_queue_key, {record["task_id"]: score})
            pipe.execute()
        self._project(
            "Task",
            "id",
            record["task_id"],
            {
                "state": "queued",
                "epoch": record["epoch"],
                "priority": record["priority"],
            },
        )
        return record

    def claim_task(
        self,
        *,
        node_id: str,
        capabilities: Iterable[str],
        epoch: int,
        ttl_ms: int | None = None,
    ) -> dict[str, Any] | None:
        if int(epoch) != self.current_epoch():
            raise ContinuityConflict("task claim epoch is stale")
        capset = {str(value) for value in capabilities}
        for task_id in self.client.zrange(self.task_queue_key, 0, 99):
            for _ in range(4):
                with self.client.pipeline() as pipe:
                    try:
                        pipe.watch(self.tasks_key)
                        raw = pipe.hget(self.tasks_key, task_id)
                        if not raw:
                            break
                        task = json.loads(raw)
                        current = now_ms()
                        unsuitable = (
                            task["state"] != "queued"
                            or task["expires_at_ms"] <= current
                            or not set(
                                task["required_capabilities"]
                            ).issubset(capset)
                        )
                        if unsuitable:
                            break
                        task.update(
                            {
                                "state": "claimed",
                                "claimed_by": node_id,
                                "claim_token": uuid.uuid4().hex,
                                "claimed_at_ms": current,
                                "claim_expires_at_ms": current
                                + max(
                                    10_000,
                                    min(
                                        int(
                                            ttl_ms
                                            or self.config.task_claim_ttl_ms
                                        ),
                                        900_000,
                                    ),
                                ),
                            }
                        )
                        pipe.multi()
                        pipe.hset(
                            self.tasks_key,
                            task_id,
                            json.dumps(task, separators=(",", ":")),
                        )
                        pipe.zrem(self.task_queue_key, task_id)
                        pipe.execute()
                        self._project(
                            "Task",
                            "id",
                            task_id,
                            {
                                "state": "claimed",
                                "claimed_by": node_id,
                            },
                        )
                        return task
                    except WatchError:
                        continue
        return None

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
        for _ in range(8):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(self.tasks_key)
                    raw = pipe.hget(self.tasks_key, task_id)
                    if not raw:
                        raise ContinuityRejected("continuity task not found")
                    task = json.loads(raw)
                    if task.get("claimed_by") != node_id:
                        raise ContinuityConflict(
                            "continuity task claimant mismatch"
                        )
                    supplied = str(task.get("claim_token") or "")
                    if not hmac.compare_digest(
                        supplied,
                        str(claim_token or ""),
                    ):
                        raise ContinuityConflict(
                            "continuity task claim token mismatch"
                        )
                    if int(task.get("claim_expires_at_ms") or 0) <= now_ms():
                        raise ContinuityConflict(
                            "continuity task claim expired"
                        )
                    task.update(
                        {
                            "state": status,
                            "result": _object(result or {}, "result"),
                            "completed_at_ms": now_ms(),
                        }
                    )
                    pipe.multi()
                    pipe.hset(
                        self.tasks_key,
                        task_id,
                        json.dumps(task, separators=(",", ":")),
                    )
                    pipe.execute()
                    self._project(
                        "Task",
                        "id",
                        task_id,
                        {
                            "state": status,
                            "completed_at_ms": task["completed_at_ms"],
                        },
                    )
                    return task
                except WatchError:
                    continue
        raise ContinuityConflict("continuity task completion was contended")

    def put_document(
        self,
        *,
        name: str,
        payload: Mapping[str, Any],
        epoch: int,
        ttl_ms: int,
    ) -> dict[str, Any]:
        record = self._reference().put_document(
            name=name,
            payload=payload,
            epoch=epoch,
            ttl_ms=ttl_ms,
        )
        self.client.hset(
            self.documents_key,
            name,
            json.dumps(record, separators=(",", ":")),
        )
        return record

    def get_document(self, name: str) -> dict[str, Any] | None:
        raw = self.client.hget(self.documents_key, name)
        if not raw:
            return None
        record = json.loads(raw)
        if (
            record["expires_at_ms"] <= now_ms()
            or record["epoch"] != self.current_epoch()
        ):
            self.client.hdel(self.documents_key, name)
            return None
        return record

    def put_context_manifest(
        self,
        manifest: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = self._reference().put_context_manifest(manifest)
        self.client.hset(
            self.contexts_key,
            record["cache_id"],
            json.dumps(record, separators=(",", ":")),
        )
        self._project(
            "ContextManifest",
            "id",
            record["cache_id"],
            {
                "prefix_id": record["prefix_id"],
                "model_id": record["model_id"],
                "node_id": record["node_id"],
                "expires_at_ms": record["expires_at_ms"],
            },
        )
        return record

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
        values = []
        for raw in self.client.hvals(self.contexts_key):
            record = json.loads(raw)
            if (
                record["expires_at_ms"] <= current
                or record["prefix_id"] != prefix_id
                or record["model_id"] != model_id
                or record["scope_id"] != scope_id
            ):
                continue
            if (
                compatibility_fingerprint
                and record["compatibility_fingerprint"]
                != compatibility_fingerprint
            ):
                continue
            values.append(record)
        values.sort(key=lambda item: item["last_used_at_ms"], reverse=True)
        return values[: max(1, min(int(limit), 100))]

    def pending_durable_events(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        values = []
        upper = max(0, min(int(limit), 1000) - 1)
        for event_id in self.client.zrange(self.pending_key, 0, upper):
            raw = self.client.get(f"{self.prefix}:event:{event_id}")
            if raw:
                values.append(json.loads(raw))
        return values

    def mark_event_committed(
        self,
        event_id: str,
        *,
        committed_at_ms: int | None = None,
    ) -> None:
        key = f"{self.prefix}:event:{event_id}"
        for _ in range(8):
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if not raw:
                        raise ContinuityRejected(
                            "continuity event not found"
                        )
                    record = json.loads(raw)
                    record["commit_state"] = "committed"
                    record["committed_at_ms"] = int(
                        committed_at_ms or now_ms()
                    )
                    pipe.multi()
                    pipe.set(
                        key,
                        json.dumps(record, separators=(",", ":")),
                        ex=604_800,
                    )
                    pipe.zrem(self.pending_key, event_id)
                    pipe.execute()
                    return
                except WatchError:
                    continue
        raise ContinuityConflict(
            "event commit acknowledgement was contended"
        )

    def snapshot(self) -> dict[str, Any]:
        current = now_ms()
        tasks = []
        for raw in self.client.hvals(self.tasks_key):
            task = json.loads(raw)
            if (
                task["expires_at_ms"] > current
                and task["state"] in {"queued", "claimed"}
            ):
                tasks.append(task)
        documents = {}
        epoch = self.current_epoch()
        for name, raw in self.client.hgetall(self.documents_key).items():
            record = json.loads(raw)
            if (
                record["expires_at_ms"] > current
                and record["epoch"] == epoch
            ):
                documents[name] = record
        return {
            "schema_version": SCHEMA_VERSION,
            "cluster_id": self.config.cluster_id,
            "node_id": self.config.node_id,
            "epoch": epoch,
            "epoch_fence_proof": str(
                self.client.get(self.proof_key) or ""
            ),
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
            "pending_durable_events": int(
                self.client.zcard(self.pending_key)
            ),
            "context_manifest_count": int(
                self.client.hlen(self.contexts_key)
            ),
            "memory": self.memory_info(),
        }

    def _project(
        self,
        label: str,
        identity_key: str,
        identity: str,
        props: Mapping[str, Any],
    ) -> None:
        if not self.config.graph_projection_enabled:
            return

        def literal(value: Any) -> str:
            if value is None:
                return "null"
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, (int, float)):
                return str(value)
            escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
            return f"'{escaped}'"

        assignments = ", ".join(
            f"n.{key} = {literal(value)}"
            for key, value in props.items()
        )
        query = (
            f"MERGE (n:{label} "
            f"{{{identity_key}: {literal(identity)}}})"
        )
        if assignments:
            query += f" SET {assignments}"
        try:
            self.client.execute_command(
                "GRAPH.QUERY",
                self.config.graph_name,
                query,
                "--compact",
            )
        except Exception:
            return
