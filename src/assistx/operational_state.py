from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class RedisCommands(Protocol):
    def execute_command(self, *args: Any) -> Any: ...


FINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED", "REJECTED", "ROLLED_BACK"}
VOLATILE_KINDS = {
    "claim",
    "heartbeat",
    "lease",
    "route_observation",
    "runtime_health",
    "session_context",
    "kv_manifest",
    "recovery_intent",
    "delegation",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _identity(kind: str, logical_id: str) -> str:
    digest = hashlib.sha256(f"{kind}:{logical_id}".encode()).hexdigest()[:32]
    return f"op-{digest}"


@dataclass(frozen=True)
class OperationalRecord:
    record_id: str
    kind: str
    logical_id: str
    state: str
    owner: str | None
    epoch: int
    payload: dict[str, Any]
    created_at_ms: int
    updated_at_ms: int
    expires_at_ms: int
    checksum: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "logical_id": self.logical_id,
            "state": self.state,
            "owner": self.owner,
            "epoch": self.epoch,
            "payload": self.payload,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "checksum": self.checksum,
        }


class OperationalStateStore:
    """FalkorDB-backed volatile coordination state.

    This store is deliberately not a second durable authority. It owns bounded,
    reconstructable operational state while Neo4j remains the destination for
    final outcomes, audit evidence, and long-lived fleet identity.
    """

    def __init__(
        self,
        client: RedisCommands,
        *,
        graph: str = "assistx_operational",
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.client = client
        self.graph = graph
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def upsert(
        self,
        *,
        kind: str,
        logical_id: str,
        state: str,
        payload: Mapping[str, Any] | None = None,
        owner: str | None = None,
        epoch: int = 0,
        ttl_seconds: int = 300,
    ) -> OperationalRecord:
        kind = str(kind).strip().lower()
        logical_id = str(logical_id).strip()
        state = str(state).strip().upper()
        if kind not in VOLATILE_KINDS:
            raise ValueError(f"unsupported operational state kind: {kind}")
        if not logical_id:
            raise ValueError("logical_id is required")
        ttl_seconds = max(5, min(int(ttl_seconds), 86_400))
        now = self.clock_ms()
        record_id = _identity(kind, logical_id)
        body = {
            "record_id": record_id,
            "kind": kind,
            "logical_id": logical_id,
            "state": state,
            "owner": owner,
            "epoch": max(0, int(epoch)),
            "payload": dict(payload or {}),
            "updated_at_ms": now,
            "expires_at_ms": now + ttl_seconds * 1000,
        }
        checksum = hashlib.sha256(_canonical(body).encode()).hexdigest()
        query = (
            "MERGE (r:OperationalRecord {record_id: $record_id}) "
            "ON CREATE SET r.created_at_ms = $now "
            "SET r.kind = $kind, r.logical_id = $logical_id, r.state = $state, "
            "r.owner = $owner, r.epoch = $epoch, r.payload_json = $payload_json, "
            "r.updated_at_ms = $now, r.expires_at_ms = $expires_at_ms, "
            "r.checksum = $checksum "
            "RETURN r.created_at_ms"
        )
        params = {
            **body,
            "now": now,
            "payload_json": _canonical(body["payload"]),
            "checksum": checksum,
        }
        response = self.client.execute_command(
            "GRAPH.QUERY", self.graph, query, "--compact", "PARAMS", _canonical(params)
        )
        created = self._extract_created_at(response, now)
        return OperationalRecord(created_at_ms=created, checksum=checksum, **body)

    def acquire_lease(
        self,
        *,
        logical_id: str,
        owner: str,
        epoch: int,
        ttl_seconds: int = 30,
        payload: Mapping[str, Any] | None = None,
    ) -> OperationalRecord:
        if not owner:
            raise ValueError("owner is required")
        current = self.get("lease", logical_id)
        now = self.clock_ms()
        if current and current.expires_at_ms > now:
            if current.owner != owner:
                raise RuntimeError("lease is held by another owner")
            if int(epoch) < current.epoch:
                raise RuntimeError("lease epoch moved backwards")
        return self.upsert(
            kind="lease",
            logical_id=logical_id,
            state="ACTIVE",
            owner=owner,
            epoch=epoch,
            ttl_seconds=ttl_seconds,
            payload=payload,
        )

    def get(self, kind: str, logical_id: str) -> OperationalRecord | None:
        record_id = _identity(str(kind).lower(), str(logical_id))
        query = (
            "MATCH (r:OperationalRecord {record_id: $record_id}) "
            "RETURN r.record_id, r.kind, r.logical_id, r.state, r.owner, r.epoch, "
            "r.payload_json, r.created_at_ms, r.updated_at_ms, r.expires_at_ms, r.checksum"
        )
        response = self.client.execute_command(
            "GRAPH.RO_QUERY",
            self.graph,
            query,
            "--compact",
            "PARAMS",
            _canonical({"record_id": record_id}),
        )
        row = self._extract_row(response)
        if not row:
            return None
        record = OperationalRecord(
            record_id=str(row[0]),
            kind=str(row[1]),
            logical_id=str(row[2]),
            state=str(row[3]),
            owner=str(row[4]) if row[4] not in (None, "") else None,
            epoch=int(row[5] or 0),
            payload=json.loads(str(row[6] or "{}")),
            created_at_ms=int(row[7] or 0),
            updated_at_ms=int(row[8] or 0),
            expires_at_ms=int(row[9] or 0),
            checksum=str(row[10] or ""),
        )
        if record.expires_at_ms <= self.clock_ms():
            return None
        return record

    def sweep_expired(self, *, limit: int = 1000) -> int:
        now = self.clock_ms()
        query = (
            "MATCH (r:OperationalRecord) WHERE r.expires_at_ms <= $now "
            "WITH r LIMIT $limit DELETE r RETURN count(r)"
        )
        response = self.client.execute_command(
            "GRAPH.QUERY",
            self.graph,
            query,
            "--compact",
            "PARAMS",
            _canonical({"now": now, "limit": max(1, min(int(limit), 10_000))}),
        )
        row = self._extract_row(response)
        return int(row[0]) if row else 0

    def finalize(
        self,
        record: OperationalRecord,
        *,
        final_state: str,
        neo4j_commit: Callable[[dict[str, Any]], Any],
        evidence: Mapping[str, Any] | None = None,
    ) -> Any:
        final_state = str(final_state).upper()
        if final_state not in FINAL_STATES:
            raise ValueError(f"unsupported final state: {final_state}")
        envelope = record.as_dict()
        envelope.update(
            {
                "final_state": final_state,
                "finalized_at_ms": self.clock_ms(),
                "evidence": dict(evidence or {}),
                "commit_id": f"commit-{uuid.uuid4().hex}",
            }
        )
        # Neo4j commits first. Operational state is only marked finalized after
        # the durable transaction succeeds, preventing volatile success from
        # becoming the source of truth.
        result = neo4j_commit(envelope)
        self.upsert(
            kind=record.kind,
            logical_id=record.logical_id,
            state=final_state,
            owner=record.owner,
            epoch=record.epoch,
            payload={"durable_commit_id": envelope["commit_id"]},
            ttl_seconds=60,
        )
        return result

    @staticmethod
    def _extract_created_at(response: Any, fallback: int) -> int:
        row = OperationalStateStore._extract_row(response)
        return int(row[0]) if row and row[0] is not None else fallback

    @staticmethod
    def _extract_row(response: Any) -> list[Any] | None:
        if not isinstance(response, (list, tuple)) or len(response) < 2:
            return None
        rows = response[1]
        if not isinstance(rows, (list, tuple)) or not rows:
            return None
        first = rows[0]
        return list(first) if isinstance(first, (list, tuple)) else None
