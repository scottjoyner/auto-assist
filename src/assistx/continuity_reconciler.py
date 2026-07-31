from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Mapping
from typing import Any, Protocol

from .continuity_state import ContinuityStore, create_store_from_env, now_ms

try:  # Optional in unit tests.
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover
    GraphDatabase = None  # type: ignore[assignment]


class DurableEventSink(Protocol):
    def commit(self, event: Mapping[str, Any]) -> None: ...


class Neo4jContinuitySink:
    """Commit final continuity events into Neo4j with event-level idempotency."""

    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        if GraphDatabase is None:
            raise RuntimeError("neo4j package is required for Neo4jContinuitySink")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self) -> None:
        self.driver.close()

    def commit(self, event: Mapping[str, Any]) -> None:
        payload = dict(event.get("payload") or {})
        params = {
            "event_id": str(event["event_id"]),
            "cluster_id": str(event["cluster_id"]),
            "source_node_id": str(event["source_node_id"]),
            "epoch": int(event["epoch"]),
            "kind": str(event["kind"]),
            "durability": str(event["durability"]),
            "idempotency_key": str(event["idempotency_key"]),
            "payload_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            "created_at_ms": int(event["created_at_ms"]),
            "committed_at_ms": now_ms(),
        }
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._write_event, params, payload)

    @staticmethod
    def _write_event(tx, params: dict[str, Any], payload: dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (e:ContinuityEvent {event_id: $event_id})
            ON CREATE SET
              e.cluster_id = $cluster_id,
              e.source_node_id = $source_node_id,
              e.epoch = $epoch,
              e.kind = $kind,
              e.durability = $durability,
              e.idempotency_key = $idempotency_key,
              e.payload_json = $payload_json,
              e.created_at_ms = $created_at_ms
            SET e.committed_at_ms = $committed_at_ms,
                e.commit_state = 'committed'
            """,
            **params,
        ).consume()

        kind = params["kind"]
        if kind == "task.completed":
            tx.run(
                """
                MERGE (t:Task {id: $task_id})
                SET t.status = $status,
                    t.result_digest = $result_digest,
                    t.completed_at_ms = $completed_at_ms,
                    t.continuity_epoch = $epoch
                """,
                task_id=str(payload.get("task_id") or ""),
                status=str(payload.get("status") or "completed"),
                result_digest=str(payload.get("result_digest") or ""),
                completed_at_ms=int(payload.get("completed_at_ms") or params["committed_at_ms"]),
                epoch=params["epoch"],
            ).consume()
        elif kind == "recovery.epoch.advanced":
            tx.run(
                """
                MERGE (c:ContinuityCluster {id: $cluster_id})
                SET c.epoch = $epoch,
                    c.fence_proof = $fence_proof,
                    c.updated_at_ms = $updated_at_ms
                """,
                cluster_id=params["cluster_id"],
                epoch=params["epoch"],
                fence_proof=str(payload.get("fence_proof") or ""),
                updated_at_ms=params["committed_at_ms"],
            ).consume()
        elif kind == "context.manifest.registered":
            tx.run(
                """
                MERGE (c:ContextManifest {cache_id: $cache_id})
                SET c.prefix_id = $prefix_id,
                    c.model_id = $model_id,
                    c.scope_id = $scope_id,
                    c.compatibility_fingerprint = $compatibility_fingerprint,
                    c.node_id = $node_id,
                    c.expires_at_ms = $expires_at_ms,
                    c.continuity_epoch = $epoch
                """,
                cache_id=str(payload.get("cache_id") or ""),
                prefix_id=str(payload.get("prefix_id") or ""),
                model_id=str(payload.get("model_id") or ""),
                scope_id=str(payload.get("scope_id") or ""),
                compatibility_fingerprint=str(payload.get("compatibility_fingerprint") or ""),
                node_id=str(payload.get("node_id") or ""),
                expires_at_ms=int(payload.get("expires_at_ms") or 0),
                epoch=params["epoch"],
            ).consume()


def reconcile_batch(
    store: ContinuityStore,
    sink: DurableEventSink,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    pending = store.pending_durable_events(limit=limit)
    committed = []
    failed = []
    for event in pending:
        try:
            sink.commit(event)
            store.mark_event_committed(str(event["event_id"]))
            committed.append(str(event["event_id"]))
        except Exception as exc:  # one event must not block later idempotent retries
            failed.append({"event_id": event.get("event_id"), "error": str(exc)[:500]})
            break
    return {
        "attempted": len(committed) + len(failed),
        "committed": committed,
        "failed": failed,
        "remaining_hint": max(0, len(pending) - len(committed)),
    }


def sink_from_env(env: Mapping[str, str]) -> Neo4jContinuitySink:
    return Neo4jContinuitySink(
        env.get("NEO4J_URI", "bolt://neo4j:7687"),
        env.get("NEO4J_USER", "neo4j"),
        env.get("NEO4J_PASSWORD", ""),
        env.get("NEO4J_DATABASE", "neo4j"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay durable continuity events into Neo4j")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    store = create_store_from_env(os.environ)
    sink = sink_from_env(os.environ)
    try:
        while True:
            result = reconcile_batch(store, sink, limit=max(1, min(args.limit, 1000)))
            print(json.dumps(result, sort_keys=True), flush=True)
            if args.once:
                return 0 if not result["failed"] else 2
            time.sleep(max(1.0, args.interval))
    finally:
        sink.close()


if __name__ == "__main__":
    raise SystemExit(main())
