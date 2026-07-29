from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any


def controller_instance_id() -> str:
    configured = os.getenv("ASSISTX_CONTROLLER_INSTANCE_ID", "").strip()
    return configured or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class Neo4jControllerStore:
    """Durable controller leases and checkpoints with fencing-token validation."""

    def __init__(self, neo: Any) -> None:
        self.neo = neo

    def acquire(
        self,
        controller_id: str,
        instance_id: str,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> dict[str, Any] | None:
        with self.neo._session() as session:
            row = session.run(
                """
                MERGE (lease:ControllerLease {controller_id:$controller_id})
                ON CREATE SET lease.fencing_token=0, lease.expires_at_ts=0
                WITH lease,
                     coalesce(lease.owner_instance_id, '') AS previous_owner,
                     coalesce(lease.expires_at_ts, 0) AS previous_expiry
                WHERE previous_owner=$instance_id OR previous_expiry <= $now_ms
                SET lease.fencing_token =
                      CASE WHEN previous_owner=$instance_id
                           THEN coalesce(lease.fencing_token, 1)
                           ELSE coalesce(lease.fencing_token, 0) + 1 END,
                    lease.owner_instance_id=$instance_id,
                    lease.acquired_at_ts =
                      CASE WHEN previous_owner=$instance_id
                           THEN coalesce(lease.acquired_at_ts, $now_ms)
                           ELSE $now_ms END,
                    lease.renewed_at_ts=$now_ms,
                    lease.expires_at_ts=$now_ms + $ttl_ms
                RETURN properties(lease) AS lease
                """,
                {
                    "controller_id": controller_id,
                    "instance_id": instance_id,
                    "now_ms": now_ms,
                    "ttl_ms": ttl_ms,
                },
            ).single()
        return dict(row["lease"]) if row else None

    def renew(
        self,
        controller_id: str,
        instance_id: str,
        fencing_token: int,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> bool:
        with self.neo._session() as session:
            row = session.run(
                """
                MATCH (lease:ControllerLease {controller_id:$controller_id})
                WHERE lease.owner_instance_id=$instance_id
                  AND lease.fencing_token=$fencing_token
                  AND lease.expires_at_ts > $now_ms
                SET lease.renewed_at_ts=$now_ms,
                    lease.expires_at_ts=$now_ms + $ttl_ms
                RETURN lease.controller_id AS controller_id
                """,
                {
                    "controller_id": controller_id,
                    "instance_id": instance_id,
                    "fencing_token": fencing_token,
                    "now_ms": now_ms,
                    "ttl_ms": ttl_ms,
                },
            ).single()
        return row is not None

    def begin_tick(
        self,
        controller_id: str,
        instance_id: str,
        fencing_token: int,
        tick_key: str,
        *,
        now_ms: int,
    ) -> dict[str, Any]:
        with self.neo._session() as session:
            row = session.run(
                """
                MATCH (lease:ControllerLease {controller_id:$controller_id})
                WHERE lease.owner_instance_id=$instance_id
                  AND lease.fencing_token=$fencing_token
                  AND lease.expires_at_ts > $now_ms
                MERGE (checkpoint:ControllerCheckpoint {controller_id:$controller_id})
                ON CREATE SET checkpoint.last_completed_tick_key=''
                WITH checkpoint
                WHERE coalesce(checkpoint.last_completed_tick_key, '') <> $tick_key
                  AND (
                    coalesce(checkpoint.status, '') <> 'RUNNING'
                    OR coalesce(checkpoint.started_at_ts, 0) < $now_ms - $stale_ms
                    OR checkpoint.fencing_token <> $fencing_token
                  )
                SET checkpoint.status='RUNNING',
                    checkpoint.tick_key=$tick_key,
                    checkpoint.owner_instance_id=$instance_id,
                    checkpoint.fencing_token=$fencing_token,
                    checkpoint.started_at_ts=$now_ms,
                    checkpoint.updated_at_ts=$now_ms,
                    checkpoint.attempt=coalesce(checkpoint.attempt, 0) + 1
                RETURN properties(checkpoint) AS checkpoint
                """,
                {
                    "controller_id": controller_id,
                    "instance_id": instance_id,
                    "fencing_token": fencing_token,
                    "tick_key": tick_key,
                    "now_ms": now_ms,
                    "stale_ms": max(
                        1000,
                        int(os.getenv("ASSISTX_CONTROLLER_TICK_STALE_MS", "900000")),
                    ),
                },
            ).single()
        if row:
            return {"started": True, "checkpoint": dict(row["checkpoint"])}
        return {"started": False, "reason": "replayed_or_in_progress"}

    def finish_tick(
        self,
        controller_id: str,
        instance_id: str,
        fencing_token: int,
        tick_key: str,
        *,
        now_ms: int,
        status: str,
        result: dict[str, Any],
    ) -> bool:
        with self.neo._session() as session:
            row = session.run(
                """
                MATCH (lease:ControllerLease {controller_id:$controller_id})
                MATCH (checkpoint:ControllerCheckpoint {controller_id:$controller_id})
                WHERE lease.owner_instance_id=$instance_id
                  AND lease.fencing_token=$fencing_token
                  AND lease.expires_at_ts > $now_ms
                  AND checkpoint.owner_instance_id=$instance_id
                  AND checkpoint.fencing_token=$fencing_token
                  AND checkpoint.tick_key=$tick_key
                  AND checkpoint.status='RUNNING'
                SET checkpoint.status=$status,
                    checkpoint.result_json=$result_json,
                    checkpoint.updated_at_ts=$now_ms,
                    checkpoint.completed_at_ts=$now_ms,
                    checkpoint.last_completed_tick_key =
                      CASE WHEN $status='SUCCEEDED'
                           THEN $tick_key
                           ELSE checkpoint.last_completed_tick_key END
                RETURN checkpoint.controller_id AS controller_id
                """,
                {
                    "controller_id": controller_id,
                    "instance_id": instance_id,
                    "fencing_token": fencing_token,
                    "tick_key": tick_key,
                    "now_ms": now_ms,
                    "status": status,
                    "result_json": json.dumps(result, default=str, sort_keys=True),
                },
            ).single()
        return row is not None

    def list_status(self, *, now_ms: int | None = None) -> list[dict[str, Any]]:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        with self.neo._session() as session:
            rows = session.run(
                """
                MATCH (lease:ControllerLease)
                OPTIONAL MATCH (checkpoint:ControllerCheckpoint {
                  controller_id:lease.controller_id
                })
                RETURN properties(lease) AS lease,
                       properties(checkpoint) AS checkpoint
                ORDER BY lease.controller_id
                """
            )
            result = []
            for row in rows:
                lease = dict(row["lease"] or {})
                checkpoint = dict(row["checkpoint"] or {})
                if checkpoint.get("result_json"):
                    try:
                        checkpoint["result"] = json.loads(
                            checkpoint.pop("result_json")
                        )
                    except (TypeError, json.JSONDecodeError):
                        checkpoint["result"] = {}
                lease["is_leader"] = int(lease.get("expires_at_ts") or 0) > now_ms
                result.append(
                    {
                        "controller_id": lease.get("controller_id"),
                        "lease": lease,
                        "checkpoint": checkpoint,
                    }
                )
        return result


class DurableController:
    """Run one idempotent tick only while holding a valid fenced lease."""

    def __init__(
        self,
        controller_id: str,
        store_factory: Callable[
            [], tuple[Neo4jControllerStore, Callable[[], None]]
        ],
        *,
        instance_id: str | None = None,
        lease_seconds: int = 120,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.controller_id = controller_id
        self.store_factory = store_factory
        self.instance_id = instance_id or controller_instance_id()
        self.lease_ms = max(30, lease_seconds) * 1000
        self.clock = clock

    def run_tick(
        self,
        tick_key: str,
        work: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        store, close = self.store_factory()
        try:
            now_ms = int(self.clock() * 1000)
            lease = store.acquire(
                self.controller_id,
                self.instance_id,
                now_ms=now_ms,
                ttl_ms=self.lease_ms,
            )
            if not lease:
                return {"executed": False, "reason": "standby_not_leader"}
            token = int(lease["fencing_token"])
            begun = store.begin_tick(
                self.controller_id,
                self.instance_id,
                token,
                tick_key,
                now_ms=now_ms,
            )
            if not begun["started"]:
                return {
                    "executed": False,
                    "reason": begun["reason"],
                    "fencing_token": token,
                }
            renew_stop = threading.Event()

            def renew_lease() -> None:
                interval = max(5.0, self.lease_ms / 3000)
                while not renew_stop.wait(interval):
                    if not store.renew(
                        self.controller_id,
                        self.instance_id,
                        token,
                        now_ms=int(self.clock() * 1000),
                        ttl_ms=self.lease_ms,
                    ):
                        return

            renew_thread = None
            if hasattr(store, "renew"):
                renew_thread = threading.Thread(
                    target=renew_lease,
                    name=f"lease-renewal:{self.controller_id}",
                    daemon=True,
                )
                renew_thread.start()
            try:
                result = work()
            except Exception as exc:
                renew_stop.set()
                finished = store.finish_tick(
                    self.controller_id,
                    self.instance_id,
                    token,
                    tick_key,
                    now_ms=int(self.clock() * 1000),
                    status="FAILED",
                    result={"error": str(exc)[:1000]},
                )
                return {
                    "executed": True,
                    "ok": False,
                    "checkpointed": finished,
                    "error": str(exc)[:1000],
                    "fencing_token": token,
                }
            finally:
                renew_stop.set()
                if renew_thread:
                    renew_thread.join(timeout=1)
            finished = store.finish_tick(
                self.controller_id,
                self.instance_id,
                token,
                tick_key,
                now_ms=int(self.clock() * 1000),
                status="SUCCEEDED",
                result=result,
            )
            return {
                "executed": True,
                "ok": finished,
                "checkpointed": finished,
                "result": result,
                "fencing_token": token,
                "reason": None
                if finished
                else "leadership_lost_before_commit",
            }
        finally:
            close()


_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


def start_durable_controller_loop(
    controller: DurableController,
    work: Callable[[], dict[str, Any]],
    *,
    interval_seconds: int,
) -> None:
    interval_seconds = max(10, interval_seconds)
    with _threads_lock:
        current = _threads.get(controller.controller_id)
        if current and current.is_alive():
            return

        def loop() -> None:
            while True:
                bucket = int(controller.clock()) // interval_seconds
                controller.run_tick(f"{controller.controller_id}:{bucket}", work)
                time.sleep(interval_seconds)

        thread = threading.Thread(
            target=loop,
            name=f"controller:{controller.controller_id}",
            daemon=True,
        )
        _threads[controller.controller_id] = thread
        thread.start()
