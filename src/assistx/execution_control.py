from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

from .controller_runtime import (
    DurableController,
    Neo4jControllerStore,
    start_durable_controller_loop,
)


class ExecutionControlPlane:
    """Fenced checkpoint, cooperative preemption, and migration transitions."""

    def checkpoint(
        self,
        neo: Any,
        task_id: str,
        agent_id: str,
        claim_id: str,
        *,
        checkpoint: dict[str, Any],
        progress: float,
        estimated_remaining_seconds: int | None,
        pause: bool,
    ) -> dict[str, Any]:
        checkpoint_id = f"checkpoint-{uuid.uuid4().hex}"
        now_ms = int(time.time() * 1000)
        with neo._session() as session:
            row = session.run(
                """
                MATCH (t:Task {id:$task_id})
                WHERE t.claimed_by=$agent_id
                  AND t.claim_id=$claim_id
                  AND t.status IN ['CLAIMED','RUNNING','PAUSING']
                WITH t, coalesce(t.checkpoint_revision, 0) + 1 AS revision
                CREATE (checkpoint:TaskCheckpoint {
                  id:$checkpoint_id,
                  task_id:$task_id,
                  revision:revision,
                  claim_id:$claim_id,
                  agent_id:$agent_id,
                  checkpoint_json:$checkpoint_json,
                  progress:$progress,
                  estimated_remaining_seconds:$estimated_remaining_seconds,
                  created_at_ts:$now_ms
                })
                MERGE (t)-[:HAS_CHECKPOINT]->(checkpoint)
                SET t.checkpoint_id=$checkpoint_id,
                    t.checkpoint_revision=revision,
                    t.checkpoint_json=$checkpoint_json,
                    t.progress=$progress,
                    t.estimated_remaining_seconds=$estimated_remaining_seconds,
                    t.updated_at_ts=$now_ms,
                    t.status=CASE WHEN $pause THEN 'PAUSED' ELSE t.status END,
                    t.paused_at_ts=CASE WHEN $pause THEN $now_ms ELSE t.paused_at_ts END,
                    t.pause_reason=CASE
                      WHEN $pause THEN coalesce(t.preemption_reason, 'checkpoint_pause')
                      ELSE t.pause_reason END,
                    t.claimed_by=CASE WHEN $pause THEN null ELSE t.claimed_by END,
                    t.claim_id=CASE WHEN $pause THEN null ELSE t.claim_id END,
                    t.lease_expires_at_ts=CASE
                      WHEN $pause THEN null ELSE t.lease_expires_at_ts END
                RETURN t, checkpoint
                """,
                {
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "claim_id": claim_id,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_json": json.dumps(
                        checkpoint, default=str, sort_keys=True
                    ),
                    "progress": max(0.0, min(float(progress), 1.0)),
                    "estimated_remaining_seconds": estimated_remaining_seconds,
                    "pause": pause,
                    "now_ms": now_ms,
                },
            ).single()
        if not row:
            return {
                "checkpointed": False,
                "reason": "stale_or_non_owner_execution",
            }
        return {
            "checkpointed": True,
            "paused": pause,
            "task": self._decode_task(dict(row["t"])),
            "checkpoint": self._decode_checkpoint(dict(row["checkpoint"])),
        }

    def request_preemption(
        self,
        neo: Any,
        task_id: str,
        actor: str,
        *,
        reason: str,
        target_agent_id: str | None,
    ) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        with neo._session() as session:
            row = session.run(
                """
                MATCH (t:Task {id:$task_id})
                WHERE t.status IN ['CLAIMED','RUNNING']
                  AND coalesce(t.preemptible, false)=true
                SET t.status='PAUSING',
                    t.preemption_requested_at_ts=$now_ms,
                    t.preemption_requested_by=$actor,
                    t.preemption_reason=$reason,
                    t.migration_target_agent_id=$target_agent_id,
                    t.updated_at_ts=$now_ms
                CREATE (event:TaskMigrationEvent {
                  id:$event_id,
                  task_id:$task_id,
                  action:'preemption_requested',
                  actor:$actor,
                  from_agent_id:t.claimed_by,
                  target_agent_id:$target_agent_id,
                  reason:$reason,
                  created_at_ts:$now_ms
                })
                MERGE (t)-[:HAS_MIGRATION_EVENT]->(event)
                RETURN t
                """,
                {
                    "task_id": task_id,
                    "actor": actor,
                    "reason": reason,
                    "target_agent_id": target_agent_id,
                    "event_id": f"migration-{uuid.uuid4().hex}",
                    "now_ms": now_ms,
                },
            ).single()
        if not row:
            return {
                "requested": False,
                "reason": "task_not_running_or_not_preemptible",
            }
        return {"requested": True, "task": self._decode_task(dict(row["t"]))}

    def migrate(
        self,
        neo: Any,
        task_id: str,
        target_agent_id: str,
        actor: str,
    ) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        with neo._session() as session:
            row = session.run(
                """
                MATCH (t:Task {id:$task_id, status:'PAUSED'})
                MATCH (target:SwarmNode {node_id:$target_agent_id})
                WHERE t.checkpoint_id IS NOT NULL
                  AND coalesce(t.migration_count, 0) <
                      coalesce(t.max_migrations, 2)
                  AND coalesce(target.is_blocked, false)=false
                  AND toLower(coalesce(target.status, 'online')) IN
                      ['online','healthy','ready']
                WITH t, coalesce(t.migration_count, 0) + 1 AS generation
                SET t.status='READY',
                    t.target_agent_id=$target_agent_id,
                    t.migration_target_agent_id=$target_agent_id,
                    t.migration_count=generation,
                    t.migration_generation=generation,
                    t.resume_required=true,
                    t.preemption_requested_at_ts=null,
                    t.preemption_requested_by=null,
                    t.updated_at_ts=$now_ms
                CREATE (event:TaskMigrationEvent {
                  id:$event_id,
                  task_id:$task_id,
                  action:'migration_scheduled',
                  actor:$actor,
                  target_agent_id:$target_agent_id,
                  checkpoint_id:t.checkpoint_id,
                  generation:generation,
                  created_at_ts:$now_ms
                })
                MERGE (t)-[:HAS_MIGRATION_EVENT]->(event)
                RETURN t
                """,
                {
                    "task_id": task_id,
                    "target_agent_id": target_agent_id,
                    "actor": actor,
                    "event_id": f"migration-{uuid.uuid4().hex}",
                    "now_ms": now_ms,
                },
            ).single()
        if not row:
            return {
                "migrated": False,
                "reason": "not_paused_checkpointed_or_budget_exhausted",
            }
        return {"migrated": True, "task": self._decode_task(dict(row["t"]))}

    def reconcile(
        self,
        neo: Any,
        *,
        now_ms: int | None = None,
        preemption_timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now_ms - max(30, preemption_timeout_seconds) * 1000
        with neo._session() as session:
            timed_out = session.run(
                """
                MATCH (t:Task {status:'PAUSING'})
                WHERE coalesce(t.preemption_requested_at_ts, 0) < $cutoff
                WITH t, t.claimed_by AS prior_owner
                SET t.status=CASE
                      WHEN coalesce(t.lease_expires_at_ts, 0) > $now_ms
                      THEN 'RUNNING' ELSE 'READY' END,
                    t.claimed_by=CASE
                      WHEN coalesce(t.lease_expires_at_ts, 0) > $now_ms
                      THEN t.claimed_by ELSE null END,
                    t.claim_id=CASE
                      WHEN coalesce(t.lease_expires_at_ts, 0) > $now_ms
                      THEN t.claim_id ELSE null END,
                    t.lease_expires_at_ts=CASE
                      WHEN coalesce(t.lease_expires_at_ts, 0) > $now_ms
                      THEN t.lease_expires_at_ts ELSE null END,
                    t.preemption_requested_at_ts=null,
                    t.preemption_requested_by=null,
                    t.preemption_reason=null,
                    t.migration_target_agent_id=null,
                    t.updated_at_ts=$now_ms
                CREATE (event:TaskMigrationEvent {
                  id:randomUUID(),
                  task_id:t.id,
                  action:'preemption_timed_out',
                  actor:'execution-reconciler',
                  from_agent_id:prior_owner,
                  created_at_ts:$now_ms
                })
                MERGE (t)-[:HAS_MIGRATION_EVENT]->(event)
                RETURN count(t) AS count
                """,
                {"cutoff": cutoff, "now_ms": now_ms},
            ).single()
            candidates = [
                dict(row)
                for row in session.run(
                    """
                    MATCH (t:Task {status:'PAUSED'})
                    WHERE t.checkpoint_id IS NOT NULL
                      AND t.migration_target_agent_id IS NOT NULL
                    RETURN t.id AS task_id,
                           t.migration_target_agent_id AS target_agent_id
                    ORDER BY t.paused_at_ts
                    LIMIT 100
                    """,
                    {},
                )
            ]
        migrated = 0
        blocked = []
        for candidate in candidates:
            result = self.migrate(
                neo,
                candidate["task_id"],
                candidate["target_agent_id"],
                "execution-reconciler",
            )
            if result["migrated"]:
                migrated += 1
            else:
                blocked.append(candidate["task_id"])
        return {
            "timed_out": int(timed_out["count"] if timed_out else 0),
            "migrated": migrated,
            "blocked": blocked,
            "considered": len(candidates),
        }

    def list_events(
        self, neo: Any, task_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with neo._session() as session:
            rows = session.run(
                """
                MATCH (event:TaskMigrationEvent)
                WHERE $task_id IS NULL OR event.task_id=$task_id
                RETURN event
                ORDER BY event.created_at_ts DESC
                LIMIT $limit
                """,
                {"task_id": task_id, "limit": max(1, min(limit, 500))},
            )
            return [dict(row["event"]) for row in rows]

    @staticmethod
    def _decode_task(task: dict[str, Any]) -> dict[str, Any]:
        if task.get("checkpoint_json"):
            try:
                task["checkpoint"] = json.loads(task["checkpoint_json"])
            except (TypeError, json.JSONDecodeError):
                task["checkpoint"] = {}
        return task

    @staticmethod
    def _decode_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
        try:
            checkpoint["checkpoint"] = json.loads(
                checkpoint.pop("checkpoint_json", "{}")
            )
        except (TypeError, json.JSONDecodeError):
            checkpoint["checkpoint"] = {}
        return checkpoint


def start_execution_reconciler(neo_factory: Callable[[], Any]) -> None:
    interval = max(
        15,
        int(os.getenv("ASSISTX_EXECUTION_RECONCILE_INTERVAL_SECONDS", "30")),
    )

    def store_factory() -> tuple[Neo4jControllerStore, Callable[[], None]]:
        neo = neo_factory()
        return Neo4jControllerStore(neo), neo.close

    controller = DurableController(
        "execution-reconciler",
        store_factory,
        lease_seconds=max(60, interval * 3),
    )

    def reconcile() -> dict[str, Any]:
        neo = neo_factory()
        try:
            return ExecutionControlPlane().reconcile(neo)
        finally:
            neo.close()

    start_durable_controller_loop(
        controller,
        reconcile,
        interval_seconds=interval,
    )
