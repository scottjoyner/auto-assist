from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class RedisCommands(Protocol):
    def execute_command(self, *args: Any) -> Any: ...


FINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "REJECTED",
    "ROLLED_BACK",
}
VOLATILE_KINDS = {
    "backup_status",
    "claim",
    "control_mode",
    "delegation",
    "heartbeat",
    "kv_manifest",
    "lease",
    "memory_pressure",
    "recovery_intent",
    "route_observation",
    "runtime_health",
    "runtime_projection",
    "session_context",
}
FENCED_KINDS = {
    "claim",
    "control_mode",
    "delegation",
    "lease",
    "recovery_intent",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _identity(kind: str, logical_id: str) -> str:
    digest = hashlib.sha256(f"{kind}:{logical_id}".encode()).hexdigest()[:32]
    return f"op-{digest}"


def _commit_identity(
    record: "OperationalRecord",
    final_state: str,
    evidence: Mapping[str, Any] | None,
) -> str:
    body = {
        "record_id": record.record_id,
        "record_checksum": record.checksum,
        "epoch": record.epoch,
        "final_state": final_state,
        "evidence": dict(evidence or {}),
    }
    return "commit-" + hashlib.sha256(_canonical(body).encode()).hexdigest()


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
    """FalkorDB-backed, bounded coordination state.

    The graph contains only reconstructable operational records. Neo4j remains
    the durable authority for final outcomes, audit evidence, and long-lived
    fleet identity. Fenced records use one server-side conditional query so two
    controllers cannot both win through a read-then-write race.
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
        body, params = self._record_parameters(
            kind=kind,
            logical_id=logical_id,
            state=state,
            payload=payload,
            owner=owner,
            epoch=epoch,
            ttl_seconds=ttl_seconds,
        )
        query = (
            "MERGE (r:OperationalRecord {record_id: $record_id}) "
            "ON CREATE SET r.created_at_ms = $now "
            "SET r.kind = $kind, r.logical_id = $logical_id, r.state = $state, "
            "r.owner = $owner, r.epoch = $epoch, r.payload_json = $payload_json, "
            "r.updated_at_ms = $now, r.expires_at_ms = $expires_at_ms, "
            "r.checksum = $checksum "
            "RETURN r.created_at_ms"
        )
        response = self._query(query, params)
        created = self._extract_created_at(response, params["now"])
        return OperationalRecord(
            created_at_ms=created,
            checksum=params["checksum"],
            **body,
        )

    def upsert_fenced(
        self,
        *,
        kind: str,
        logical_id: str,
        state: str,
        owner: str,
        epoch: int,
        payload: Mapping[str, Any] | None = None,
        ttl_seconds: int = 300,
    ) -> OperationalRecord:
        normalized_kind = str(kind).strip().lower()
        if normalized_kind not in FENCED_KINDS:
            raise ValueError(f"operational kind is not fenced: {normalized_kind}")
        if not str(owner or "").strip():
            raise ValueError("owner is required")
        body, params = self._record_parameters(
            kind=normalized_kind,
            logical_id=logical_id,
            state=state,
            payload=payload,
            owner=owner,
            epoch=epoch,
            ttl_seconds=ttl_seconds,
        )
        query = (
            "MERGE (r:OperationalRecord {record_id: $record_id}) "
            "ON CREATE SET r.created_at_ms = $now, r.epoch = -1, "
            "r.expires_at_ms = 0, r.owner = null "
            "WITH r "
            "WHERE r.expires_at_ms <= $now OR "
            "(r.owner = $owner AND coalesce(r.epoch, -1) <= $epoch) "
            "SET r.kind = $kind, r.logical_id = $logical_id, r.state = $state, "
            "r.owner = $owner, r.epoch = $epoch, r.payload_json = $payload_json, "
            "r.updated_at_ms = $now, r.expires_at_ms = $expires_at_ms, "
            "r.checksum = $checksum "
            "RETURN r.record_id, r.created_at_ms"
        )
        response = self._query(query, params)
        row = self._extract_row(response)
        if not row:
            current = self.get(normalized_kind, logical_id)
            if current and current.owner != owner:
                raise RuntimeError("fenced record is held by another owner")
            raise RuntimeError("fenced record epoch moved backwards")
        return OperationalRecord(
            created_at_ms=int(row[1] or params["now"]),
            checksum=params["checksum"],
            **body,
        )

    def acquire_lease(
        self,
        *,
        logical_id: str,
        owner: str,
        epoch: int,
        ttl_seconds: int = 30,
        payload: Mapping[str, Any] | None = None,
    ) -> OperationalRecord:
        return self.upsert_fenced(
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
            "r.payload_json, r.created_at_ms, r.updated_at_ms, "
            "r.expires_at_ms, r.checksum"
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
        record = self._record_from_row(row)
        if record.expires_at_ms <= self.clock_ms():
            return None
        return record

    def delete(self, kind: str, logical_id: str) -> bool:
        record_id = _identity(str(kind).lower(), str(logical_id))
        response = self._query(
            "MATCH (r:OperationalRecord {record_id: $record_id}) "
            "DELETE r RETURN count(r)",
            {"record_id": record_id},
        )
        row = self._extract_row(response)
        return bool(row and int(row[0] or 0) > 0)

    def sweep_expired(self, *, limit: int = 1000) -> int:
        now = self.clock_ms()
        query = (
            "MATCH (r:OperationalRecord) WHERE r.expires_at_ms <= $now "
            "WITH r LIMIT $limit DELETE r RETURN count(r)"
        )
        response = self._query(
            query,
            {"now": now, "limit": max(1, min(int(limit), 10_000))},
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
        normalized_state = str(final_state).upper()
        if normalized_state not in FINAL_STATES:
            raise ValueError(f"unsupported final state: {normalized_state}")
        commit_id = _commit_identity(record, normalized_state, evidence)
        envelope = record.as_dict()
        envelope.update(
            {
                "final_state": normalized_state,
                "finalized_at_ms": self.clock_ms(),
                "evidence": dict(evidence or {}),
                "commit_id": commit_id,
            }
        )
        # Durable commit first. A retry produces the same commit_id, allowing
        # Neo4j MERGE semantics to make replay idempotent.
        result = neo4j_commit(envelope)
        self.upsert(
            kind=record.kind,
            logical_id=record.logical_id,
            state=normalized_state,
            owner=record.owner,
            epoch=record.epoch,
            payload={"durable_commit_id": commit_id},
            ttl_seconds=60,
        )
        return result

    def _record_parameters(
        self,
        *,
        kind: str,
        logical_id: str,
        state: str,
        payload: Mapping[str, Any] | None,
        owner: str | None,
        epoch: int,
        ttl_seconds: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        normalized_kind = str(kind).strip().lower()
        normalized_id = str(logical_id).strip()
        normalized_state = str(state).strip().upper()
        if normalized_kind not in VOLATILE_KINDS:
            raise ValueError(
                f"unsupported operational state kind: {normalized_kind}"
            )
        if not normalized_id:
            raise ValueError("logical_id is required")
        bounded_ttl = max(5, min(int(ttl_seconds), 86_400))
        now = self.clock_ms()
        body = {
            "record_id": _identity(normalized_kind, normalized_id),
            "kind": normalized_kind,
            "logical_id": normalized_id,
            "state": normalized_state,
            "owner": str(owner) if owner not in (None, "") else None,
            "epoch": max(0, int(epoch)),
            "payload": dict(payload or {}),
            "updated_at_ms": now,
            "expires_at_ms": now + bounded_ttl * 1000,
        }
        checksum = hashlib.sha256(_canonical(body).encode()).hexdigest()
        params = {
            **body,
            "now": now,
            "payload_json": _canonical(body["payload"]),
            "checksum": checksum,
        }
        return body, params

    def _query(self, query: str, params: Mapping[str, Any]) -> Any:
        return self.client.execute_command(
            "GRAPH.QUERY",
            self.graph,
            query,
            "--compact",
            "PARAMS",
            _canonical(dict(params)),
        )

    @staticmethod
    def _record_from_row(row: list[Any]) -> OperationalRecord:
        return OperationalRecord(
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
