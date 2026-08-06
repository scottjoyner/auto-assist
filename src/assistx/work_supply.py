"""Capacity- and priority-aware background work-supply authority."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .controller_runtime import (
    DurableController,
    Neo4jControllerStore,
    start_durable_controller_loop,
)
from .neo4j_client import Neo4jClient
from .runtime_projection import RuntimeProjectionBlocked, build_runtime_projection

logger = logging.getLogger(__name__)

WORK_SUPPLY_INTERVAL = max(
    10, int(os.getenv("ASSISTX_WORK_SUPPLY_INTERVAL_SECONDS", "15"))
)
BACKGROUND_BACKLOG_PER_FREE_SLOT = max(
    1, min(50, int(os.getenv("ASSISTX_BACKGROUND_BACKLOG_PER_FREE_SLOT", "4")))
)
MAX_BACKGROUND_BACKLOG = max(
    1, min(1000, int(os.getenv("ASSISTX_MAX_BACKGROUND_BACKLOG", "200")))
)
STATE_FRESHNESS_SECONDS = max(
    WORK_SUPPLY_INTERVAL * 3,
    int(os.getenv("ASSISTX_WORK_SUPPLY_STATE_FRESHNESS_SECONDS", "60")),
)
_PRIORITY_NAMES = {"critical", "repo_critical", "interactive", "high"}
_ACTIVE_STATUSES = {"CLAIMED", "RUNNING", "PAUSING"}

_started = False
_start_lock = threading.Lock()
_installed = False
_install_lock = threading.Lock()


@dataclass(frozen=True)
class WorkSupplyDecision:
    generated_at_ts: int
    projection_generation: int
    total_slots: int
    active_tasks: int
    free_slots: int
    priority_ready: int
    background_ready: int
    target_background_ready: int
    allow_background: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _projection_capacity(neo_factory: Callable[[], Any]) -> tuple[int, int]:
    document = build_runtime_projection(
        neo_factory,
        secret="assistx-internal-work-supply-projection",
        ttl_seconds=max(10, WORK_SUPPLY_INTERVAL * 4),
    )
    runtime_slots: dict[str, int] = {}
    for provider in document.get("providers") or []:
        if not isinstance(provider, dict) or not provider.get("enabled", True):
            continue
        runtime_id = str(provider.get("runtime_instance_id") or "").strip()
        slots = int(provider.get("parallel_slots") or 0)
        if runtime_id and slots > 0:
            runtime_slots[runtime_id] = slots
    return int(document.get("generation") or 0), sum(runtime_slots.values())


def _task_pressure(neo: Any) -> tuple[int, int, int]:
    with neo._session() as session:
        rows = session.run(
            """
            MATCH (t:Task)
            WHERE t.status IN ['READY','CLAIMED','RUNNING','PAUSING']
            RETURN t.status AS status,
                   toLower(coalesce(t.priority, 'background')) AS priority,
                   count(t) AS count
            """
        )
        active = 0
        priority_ready = 0
        background_ready = 0
        for row in rows:
            status = str(row["status"] or "")
            priority = str(row["priority"] or "background")
            count = int(row["count"] or 0)
            if status in _ACTIVE_STATUSES:
                active += count
            elif status == "READY":
                if priority in _PRIORITY_NAMES:
                    priority_ready += count
                else:
                    background_ready += count
        return active, priority_ready, background_ready


def compute_work_supply_decision(
    neo_factory: Callable[[], Any] = Neo4jClient,
) -> WorkSupplyDecision:
    now_ms = int(time.time() * 1000)
    try:
        generation, total_slots = _projection_capacity(neo_factory)
    except (RuntimeProjectionBlocked, ValueError, TypeError) as exc:
        return WorkSupplyDecision(
            generated_at_ts=now_ms,
            projection_generation=0,
            total_slots=0,
            active_tasks=0,
            free_slots=0,
            priority_ready=0,
            background_ready=0,
            target_background_ready=0,
            allow_background=False,
            reason=f"projection_unavailable:{exc}",
        )

    neo = neo_factory()
    try:
        active, priority_ready, background_ready = _task_pressure(neo)
    finally:
        neo.close()
    free_slots = max(0, total_slots - active)
    target = min(
        MAX_BACKGROUND_BACKLOG,
        free_slots * BACKGROUND_BACKLOG_PER_FREE_SLOT,
    )
    if total_slots <= 0:
        allowed, reason = False, "zero_capacity"
    elif priority_ready > 0:
        allowed, reason = False, "priority_work_waiting"
    elif free_slots <= 0:
        allowed, reason = False, "no_free_slots"
    elif background_ready >= target:
        allowed, reason = False, "background_target_satisfied"
    else:
        allowed, reason = True, "idle_capacity_available"
    return WorkSupplyDecision(
        generated_at_ts=now_ms,
        projection_generation=generation,
        total_slots=total_slots,
        active_tasks=active,
        free_slots=free_slots,
        priority_ready=priority_ready,
        background_ready=background_ready,
        target_background_ready=target,
        allow_background=allowed,
        reason=reason,
    )


def _persist_decision(neo: Any, decision: WorkSupplyDecision) -> None:
    payload = decision.to_dict()
    with neo._session() as session:
        session.run(
            """
            MERGE (s:WorkSupplyState {name:'canonical'})
            SET s += $payload,
                s.updated_at_ts=$generated_at_ts
            """,
            {"payload": payload, "generated_at_ts": decision.generated_at_ts},
        ).consume()
        if not decision.allow_background:
            bucket = decision.generated_at_ts // max(60_000, WORK_SUPPLY_INTERVAL * 1000)
            session.run(
                """
                MERGE (e:WorkSupplyEvent {id:$id})
                ON CREATE SET e.created_at_ts=$created_at_ts,
                              e.action='background_yield',
                              e.reason=$reason,
                              e.priority_ready=$priority_ready,
                              e.background_ready=$background_ready,
                              e.free_slots=$free_slots,
                              e.projection_generation=$projection_generation
                """,
                {
                    "id": f"work-supply:{bucket}:{decision.reason}",
                    "created_at_ts": decision.generated_at_ts,
                    "reason": decision.reason,
                    "priority_ready": decision.priority_ready,
                    "background_ready": decision.background_ready,
                    "free_slots": decision.free_slots,
                    "projection_generation": decision.projection_generation,
                },
            ).consume()


def reconcile_work_supply() -> dict[str, Any]:
    decision = compute_work_supply_decision()
    neo = Neo4jClient()
    try:
        _persist_decision(neo, decision)
    finally:
        neo.close()
    return decision.to_dict()


def current_work_supply_decision(
    neo_factory: Callable[[], Any] = Neo4jClient,
) -> WorkSupplyDecision:
    now_ms = int(time.time() * 1000)
    neo = neo_factory()
    try:
        with neo._session() as session:
            row = session.run(
                """
                MATCH (s:WorkSupplyState {name:'canonical'})
                WHERE coalesce(s.generated_at_ts, 0) > $cutoff
                RETURN properties(s) AS state
                """,
                {"cutoff": now_ms - STATE_FRESHNESS_SECONDS * 1000},
            ).single()
        if row:
            state = dict(row["state"] or {})
            return WorkSupplyDecision(
                generated_at_ts=int(state.get("generated_at_ts") or 0),
                projection_generation=int(state.get("projection_generation") or 0),
                total_slots=int(state.get("total_slots") or 0),
                active_tasks=int(state.get("active_tasks") or 0),
                free_slots=int(state.get("free_slots") or 0),
                priority_ready=int(state.get("priority_ready") or 0),
                background_ready=int(state.get("background_ready") or 0),
                target_background_ready=int(
                    state.get("target_background_ready") or 0
                ),
                allow_background=bool(state.get("allow_background")),
                reason=str(state.get("reason") or "unknown"),
            )
    finally:
        neo.close()
    return compute_work_supply_decision(neo_factory)


def start_work_supply_controller() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True

        def store_factory() -> tuple[Neo4jControllerStore, Callable[[], None]]:
            neo = Neo4jClient()
            return Neo4jControllerStore(neo), neo.close

        controller = DurableController(
            "work-supply-arbiter",
            store_factory,
            lease_seconds=max(60, WORK_SUPPLY_INTERVAL * 6),
        )
        start_durable_controller_loop(
            controller,
            reconcile_work_supply,
            interval_seconds=WORK_SUPPLY_INTERVAL,
        )
        logger.info("work supply: durable capacity arbiter started")


def install_work_supply_boundaries() -> None:
    """Attach the arbiter to existing lifespan-started producer functions."""

    global _installed
    with _install_lock:
        if _installed:
            return
        _installed = True
        from . import fleet_executor, repo_task_generator

        original_executor_start = fleet_executor._start_executor_loop
        original_repo_start = repo_task_generator.start_repo_task_generator
        original_repo_cycle = repo_task_generator.repo_task_cycle

        def executor_start() -> None:
            start_work_supply_controller()
            original_executor_start()

        def repo_start() -> None:
            start_work_supply_controller()
            original_repo_start()

        def repo_cycle() -> dict[str, Any]:
            decision = current_work_supply_decision()
            if not decision.allow_background:
                return {
                    "created": 0,
                    "reason": decision.reason,
                    "work_supply": decision.to_dict(),
                }
            result = original_repo_cycle()
            result["work_supply"] = decision.to_dict()
            return result

        fleet_executor._start_executor_loop = executor_start
        repo_task_generator.start_repo_task_generator = repo_start
        repo_task_generator.repo_task_cycle = repo_cycle
