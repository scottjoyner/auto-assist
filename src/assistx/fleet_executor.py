"""Claim-fenced continuous fleet executor.

The executor is deliberately a consumer of AssistX authority, never an
independent discovery or scheduling authority. It:

* reads only the current approved AssistX runtime projection;
* claims READY ``llm`` tasks through Neo4j's atomic claim contract;
* requires and propagates the current ``claim_id``;
* heartbeats every active attempt while inference is running;
* sends inference through auto-router with claim lineage metadata;
* runs each inference request in a supervised process that is terminated before
  capacity is released on timeout or lost ownership;
* never executes arbitrary shell strings.

Recurring execution is protected by a Neo4j ``DurableController`` lease, so
multiple API processes or hosts do not create independent executor leaders.
"""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import queue
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from .controller_runtime import (
    DurableController,
    Neo4jControllerStore,
    start_durable_controller_loop,
)
from .neo4j_client import Neo4jClient
from .runtime_projection import RuntimeProjectionBlocked, build_runtime_projection

logger = logging.getLogger(__name__)

EXECUTOR_INTERVAL = max(2, int(os.getenv("FLEET_EXECUTOR_INTERVAL", "5")))
MAX_CONCURRENT_LLM = max(
    1, min(128, int(os.getenv("FLEET_EXECUTOR_LLM_CONCURRENCY", "16")))
)
TASK_WALL_TIMEOUT = max(30, int(os.getenv("FLEET_TASK_WALL_TIMEOUT", "600")))
TASK_LEASE_SECONDS = max(
    60,
    int(os.getenv("FLEET_TASK_LEASE_SECONDS", str(TASK_WALL_TIMEOUT + 120))),
)
HEARTBEAT_INTERVAL = max(
    5,
    min(
        TASK_LEASE_SECONDS // 3,
        int(os.getenv("FLEET_TASK_HEARTBEAT_INTERVAL", "15")),
    ),
)
ROUTER_CALL_TIMEOUT = max(
    10, int(os.getenv("FLEET_ROUTER_CALL_TIMEOUT", str(TASK_WALL_TIMEOUT - 5)))
)
ROUTER_URL = os.getenv("FLEET_ROUTER_URL", "http://router:8088").rstrip("/")
ROUTER_BEARER_TOKEN = os.getenv("FLEET_ROUTER_BEARER_TOKEN", "").strip()
EXECUTOR_AGENT_ID = os.getenv("FLEET_EXECUTOR_AGENT_ID", "fleet-executor").strip()
BACKGROUND_MAX_FRACTION = min(
    0.9,
    max(0.0, float(os.getenv("FLEET_BACKGROUND_MAX_FRACTION", "0.5"))),
)
PROJECTION_TTL_SECONDS = max(
    5, min(300, int(os.getenv("FLEET_EXECUTOR_PROJECTION_TTL_SECONDS", "60")))
)
COMPLEXITY_HINTS = {"high", "medium"}
QUALITY_MODEL_FLOOR_B = float(os.getenv("FLEET_QUALITY_MODEL_FLOOR_B", "9"))

_fleet_executor_instance: Optional["FleetExecutor"] = None
_executor_start_lock = threading.Lock()
_executor_started = False


def _model_billions(model_key: str) -> float:
    """Best-effort parameter count parsed from a model identifier."""

    best = 0.0
    for match in re.finditer(
        r"(\d+(?:\.\d+)?)\s*([bm])", model_key or "", re.IGNORECASE
    ):
        value = float(match.group(1))
        billions = value / 1000.0 if match.group(2).lower() == "m" else value
        best = max(best, billions)
    return best


def _resident_quality_models(loaded_models: list[str]) -> list[str]:
    return sorted(
        [
            model
            for model in loaded_models
            if _model_billions(model) >= QUALITY_MODEL_FLOOR_B
        ],
        key=_model_billions,
        reverse=True,
    )


def _auto_weight(
    specs: Optional[dict], loaded_models: Optional[list[str]] = None
) -> int:
    """Compatibility helper retained for callers that display capacity hints.

    Runtime admission never trusts this estimate. The safe executor uses only
    approved ``parallel_slots`` from the current projection.
    """

    loaded_models = loaded_models or []
    if specs and isinstance(specs, dict):
        cores = max(0, int(specs.get("cpu_cores") or 0))
        vram = max(0.0, float(specs.get("vram_gib") or 0.0))
        return max(1, min(16, 1 + cores // 4 + int(vram >= 8)))
    biggest = max((_model_billions(model) for model in loaded_models), default=0.0)
    if biggest >= 20:
        return 8
    if biggest >= 9:
        return 6
    if biggest >= 3:
        return 4
    return 2


def _decode_json(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, type(default)) else default
        except json.JSONDecodeError:
            return default
    return default


def _task_priority(task: dict[str, Any]) -> str:
    return str(task.get("priority") or "background").strip().lower()


def _is_background(task: dict[str, Any]) -> bool:
    return _task_priority(task) in {"background", "batch", "low", "unset", ""}


def _router_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if ROUTER_BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {ROUTER_BEARER_TOKEN}"
    return headers


def _router_call_worker(
    result_queue: Any,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
) -> None:
    """Process target for one cancellable router request."""

    try:
        with httpx.Client(
            timeout=max(1, timeout_seconds),
            follow_redirects=False,
        ) as client:
            response = client.post(url, headers=headers, json=payload)
        try:
            body: Any = response.json()
        except ValueError:
            body = {"text": response.text[:4000]}
        result_queue.put(
            {
                "status_code": response.status_code,
                "body": body,
            }
        )
    except BaseException as exc:  # process boundary must always report a result
        result_queue.put(
            {
                "status_code": 0,
                "body": {"error": f"{type(exc).__name__}: {exc}"},
            }
        )


@dataclass(frozen=True)
class ProjectionInventory:
    generation: int
    revision: str
    expires_at_ms: int
    providers: tuple[dict[str, Any], ...]
    aliases: tuple[str, ...]
    total_slots: int

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "ProjectionInventory":
        now_ms = int(time.time() * 1000)
        generation = int(document.get("generation") or 0)
        expires_at_ms = int(document.get("expires_at_ms") or 0)
        providers = tuple(
            provider
            for provider in document.get("providers") or []
            if isinstance(provider, dict) and provider.get("enabled", True)
        )
        if generation <= 0 or expires_at_ms <= now_ms or not providers:
            raise RuntimeProjectionBlocked("executor projection is absent or expired")

        aliases: list[str] = []
        runtime_slots: dict[str, int] = {}
        for provider in providers:
            runtime_id = str(provider.get("runtime_instance_id") or "").strip()
            slots = int(provider.get("parallel_slots") or 0)
            if not runtime_id or slots <= 0:
                continue
            previous = runtime_slots.get(runtime_id)
            if previous is not None and previous != slots:
                raise RuntimeProjectionBlocked(
                    f"conflicting capacity for runtime {runtime_id}"
                )
            runtime_slots[runtime_id] = slots
            for model in provider.get("models") or []:
                if not isinstance(model, dict):
                    continue
                alias = str(model.get("alias") or "").strip()
                if alias and alias not in aliases:
                    aliases.append(alias)
        if not runtime_slots or not aliases:
            raise RuntimeProjectionBlocked(
                "projection has no admitted runtime capacity or model aliases"
            )
        return cls(
            generation=generation,
            revision=str(document.get("revision") or ""),
            expires_at_ms=expires_at_ms,
            providers=providers,
            aliases=tuple(aliases),
            total_slots=sum(runtime_slots.values()),
        )

    def choose_model(self, requested: str, complexity: str = "") -> str:
        requested = requested.strip()
        if requested:
            exact = next((alias for alias in self.aliases if alias == requested), None)
            if exact:
                return exact
            lowered = requested.lower()
            partial = next(
                (alias for alias in self.aliases if lowered in alias.lower()),
                None,
            )
            if partial:
                return partial
            raise RuntimeProjectionBlocked(
                f"requested model {requested!r} is not admitted by the projection"
            )
        if complexity in COMPLEXITY_HINTS:
            return max(self.aliases, key=_model_billions)
        return min(self.aliases, key=lambda value: (_model_billions(value) or 9999, value))


class FleetRouting:
    """Compatibility facade over the approved projection inventory."""

    def __init__(self) -> None:
        self._projection: ProjectionInventory | None = None

    def update(self, projection: ProjectionInventory) -> None:
        self._projection = projection

    def get_model_perf(self, hostname: str, model: str) -> dict[str, Any] | None:
        return None

    def check_model_fit(self, hostname: str, model: str) -> dict[str, Any]:
        aliases = set(self._projection.aliases if self._projection else ())
        return {
            "fits": model in aliases,
            "reason": "approved_projection" if model in aliases else "not_admitted",
        }

    def snapshot(self) -> dict[str, Any]:
        if not self._projection:
            return {"generation": 0, "providers": [], "aliases": []}
        return {
            "generation": self._projection.generation,
            "revision": self._projection.revision,
            "providers": list(self._projection.providers),
            "aliases": list(self._projection.aliases),
            "total_slots": self._projection.total_slots,
        }


class FleetExecutor:
    """Projection-driven, claim-fenced executor for continuous LLM work."""

    def __init__(
        self,
        *,
        neo_factory: Callable[[], Any] = Neo4jClient,
        projection_loader: Callable[[], dict[str, Any]] | None = None,
        router_runner: Callable[
            [dict[str, Any], int, threading.Event], dict[str, Any]
        ]
        | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.neo_factory = neo_factory
        self.projection_loader = projection_loader or self._load_projection
        self.router_runner = router_runner or self._run_router_supervised
        self.clock = clock
        self._pool = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_LLM,
            thread_name_prefix="safe-fleet-task",
        )
        self._lock = threading.RLock()
        self._active: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._projection: ProjectionInventory | None = None
        self._routing = FleetRouting()
        self._stop = threading.Event()

    def _load_projection(self) -> dict[str, Any]:
        # This internal read uses the same approved evidence contract as the
        # signed router projection. The router independently verifies the signed
        # schema-v2 document before forwarding inference.
        return build_runtime_projection(
            self.neo_factory,
            secret="assistx-internal-executor-projection",
            ttl_seconds=PROJECTION_TTL_SECONDS,
        )

    def _list_ready_llm_tasks(self, neo: Any, limit: int) -> list[dict[str, Any]]:
        with neo._session() as session:
            rows = session.run(
                """
                MATCH (t:Task {status:'READY'})
                WHERE 'llm' IN coalesce(t.required_capabilities, [])
                  AND coalesce(t.requires_approval, false)=false
                RETURN t
                ORDER BY CASE toLower(coalesce(t.priority, 'background'))
                    WHEN 'critical' THEN 0
                    WHEN 'repo_critical' THEN 1
                    WHEN 'interactive' THEN 2
                    WHEN 'local_only' THEN 3
                    WHEN 'medium' THEN 4
                    WHEN 'batch' THEN 5
                    ELSE 6 END,
                    coalesce(t.created_at_ts, 0)
                LIMIT $limit
                """,
                {"limit": max(1, min(int(limit), 500))},
            )
            return [dict(row["t"]) for row in rows]

    @staticmethod
    def _payload(task: dict[str, Any]) -> dict[str, Any]:
        return _decode_json(task.get("payload") or task.get("payload_json"), {})

    @staticmethod
    def _messages(payload: dict[str, Any]) -> list[dict[str, str]]:
        raw = payload.get("messages")
        if isinstance(raw, list):
            messages = [
                {
                    "role": str(item.get("role") or "user"),
                    "content": str(item.get("content") or ""),
                }
                for item in raw
                if isinstance(item, dict) and str(item.get("content") or "").strip()
            ]
            if messages:
                return messages
        prompt = str(payload.get("prompt") or payload.get("command") or "").strip()
        return [{"role": "user", "content": prompt}] if prompt else []

    def _request_payload(
        self,
        task: dict[str, Any],
        claim_id: str,
        projection: ProjectionInventory,
    ) -> dict[str, Any]:
        payload = self._payload(task)
        messages = self._messages(payload)
        if not messages:
            raise ValueError("task does not contain messages or a prompt")
        complexity = str(
            payload.get("complexity") or payload.get("quality") or ""
        ).strip().lower()
        model = projection.choose_model(str(payload.get("model") or ""), complexity)
        metadata = dict(payload.get("metadata") or {})
        metadata["assistx_executor"] = {
            "task_id": str(task.get("id") or ""),
            "claim_id": claim_id,
            "agent_id": EXECUTOR_AGENT_ID,
            "projection_generation": projection.generation,
        }
        return {
            "model": model,
            "messages": messages,
            "temperature": float(payload.get("temperature", 0.2)),
            "max_tokens": max(1, min(int(payload.get("max_tokens", 4096)), 32768)),
            "stream": False,
            "metadata": metadata,
        }

    def _run_router_supervised(
        self,
        request_payload: dict[str, Any],
        timeout_seconds: int,
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        context = mp.get_context("spawn")
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_router_call_worker,
            args=(
                result_queue,
                f"{ROUTER_URL}/v1/chat/completions",
                _router_headers(),
                request_payload,
                min(timeout_seconds, ROUTER_CALL_TIMEOUT),
            ),
            name=f"fleet-router:{request_payload.get('model', 'unknown')}",
            daemon=True,
        )
        process.start()
        deadline = self.clock() + timeout_seconds
        try:
            while process.is_alive():
                remaining = deadline - self.clock()
                if cancel_event.is_set() or remaining <= 0:
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    return {
                        "status_code": 0,
                        "body": {
                            "error": "claim_lost"
                            if cancel_event.is_set()
                            else "task_wall_timeout"
                        },
                    }
                process.join(timeout=min(0.5, remaining))
            try:
                return result_queue.get(timeout=2)
            except queue.Empty:
                return {
                    "status_code": 0,
                    "body": {"error": "router_worker_exited_without_result"},
                }
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            result_queue.close()

    @staticmethod
    def _result_from_router(response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        status_code = int(response.get("status_code") or 0)
        body = response.get("body")
        if not isinstance(body, dict):
            body = {"response": body}
        if status_code != 200:
            return "FAILED", {
                "exit_code": 1,
                "status_code": status_code,
                "error": body,
            }
        choices = body.get("choices") or []
        content = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message, dict):
                content = str(message.get("content") or "")
        if not content.strip():
            return "FAILED", {
                "exit_code": 1,
                "status_code": status_code,
                "error": "empty_response",
                "router_response": body,
            }
        return "DONE", {
            "exit_code": 0,
            "status_code": status_code,
            "content": content,
            "model": body.get("model"),
            "usage": body.get("usage") or {},
        }

    def _execute_claimed(
        self,
        task: dict[str, Any],
        claim_id: str,
        projection: ProjectionInventory,
    ) -> None:
        task_id = str(task.get("id") or "")
        cancel_event = threading.Event()
        neo = self.neo_factory()
        try:
            request_payload = self._request_payload(task, claim_id, projection)
            holder: dict[str, Any] = {}

            def invoke() -> None:
                holder["response"] = self.router_runner(
                    request_payload,
                    TASK_WALL_TIMEOUT,
                    cancel_event,
                )

            invocation = threading.Thread(
                target=invoke,
                name=f"router-invocation:{task_id}",
                daemon=True,
            )
            invocation.start()
            while invocation.is_alive():
                invocation.join(timeout=HEARTBEAT_INTERVAL)
                if not invocation.is_alive():
                    break
                heartbeat = neo.heartbeat_task(
                    task_id,
                    EXECUTOR_AGENT_ID,
                    status="RUNNING",
                    lease_seconds=TASK_LEASE_SECONDS,
                    claim_id=claim_id,
                    metadata={
                        "executor": "safe-fleet-executor",
                        "projection_generation": projection.generation,
                        "model": request_payload["model"],
                    },
                )
                if not heartbeat:
                    logger.warning(
                        "fleet executor: claim lost while task %s was running",
                        task_id,
                    )
                    cancel_event.set()
                    invocation.join(timeout=10)
                    return

            response = holder.get(
                "response",
                {
                    "status_code": 0,
                    "body": {"error": "router_invocation_failed"},
                },
            )
            status, result = self._result_from_router(response)
            summary = str(result.get("content") or result.get("error") or "")[:1000]
            completed = neo.complete_task(
                task_id,
                EXECUTOR_AGENT_ID,
                status,
                summary=summary or None,
                result={
                    **result,
                    "projection_generation": projection.generation,
                    "projection_revision": projection.revision,
                    "claim_id": claim_id,
                },
                idempotency_key=f"safe-fleet/complete/{task_id}/{claim_id}",
                claim_id=claim_id,
            )
            if not completed:
                logger.warning(
                    "fleet executor: stale completion rejected for task %s",
                    task_id,
                )
        except Exception as exc:
            logger.exception("fleet executor: task %s failed: %s", task_id, exc)
            try:
                neo.complete_task(
                    task_id,
                    EXECUTOR_AGENT_ID,
                    "FAILED",
                    summary=str(exc)[:1000],
                    result={"exit_code": 1, "error": str(exc)[:4000]},
                    idempotency_key=f"safe-fleet/failure/{task_id}/{claim_id}",
                    claim_id=claim_id,
                )
            except Exception:
                logger.exception(
                    "fleet executor: could not persist failure for task %s", task_id
                )
        finally:
            try:
                neo.close()
            finally:
                with self._lock:
                    self._active.pop(task_id, None)
                    self._futures.pop(task_id, None)

    def _claim(
        self,
        neo: Any,
        task: dict[str, Any],
        projection: ProjectionInventory,
    ) -> bool:
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            return False
        claimed = neo.claim_task(
            task_id,
            EXECUTOR_AGENT_ID,
            capabilities=["llm"],
            lease_seconds=TASK_LEASE_SECONDS,
            idempotency_key=f"safe-fleet/claim/{task_id}",
        )
        if not claimed:
            return False
        claim_id = str(claimed.get("claim_id") or "").strip()
        if not claim_id:
            logger.error(
                "fleet executor: task %s claim did not return claim_id; refusing execution",
                task_id,
            )
            return False
        with self._lock:
            if task_id in self._active:
                return False
            self._active[task_id] = {
                "claim_id": claim_id,
                "priority": _task_priority(task),
                "projection_generation": projection.generation,
                "started_at_ts": int(self.clock() * 1000),
            }
            future = self._pool.submit(
                self._execute_claimed,
                {**task, **claimed},
                claim_id,
                projection,
            )
            self._futures[task_id] = future
        return True

    def run_once(self) -> dict[str, Any]:
        if self._stop.is_set():
            return {"executed": False, "reason": "stopping"}
        try:
            projection = ProjectionInventory.from_document(self.projection_loader())
        except Exception as exc:
            logger.warning("fleet executor: projection unavailable: %s", exc)
            return {"executed": False, "reason": "projection_unavailable", "error": str(exc)}
        self._projection = projection
        self._routing.update(projection)

        with self._lock:
            active = len(self._active)
            active_background = sum(
                1
                for item in self._active.values()
                if item.get("priority") in {"background", "batch", "low", "unset", ""}
            )
        capacity = min(MAX_CONCURRENT_LLM, projection.total_slots)
        free = max(0, capacity - active)
        if free <= 0:
            return {
                "executed": True,
                "claimed": 0,
                "active": active,
                "capacity": capacity,
                "projection_generation": projection.generation,
            }

        neo = self.neo_factory()
        claimed_count = 0
        try:
            candidates = self._list_ready_llm_tasks(neo, max(free * 4, 20))
            high_priority_waiting = any(not _is_background(task) for task in candidates)
            background_limit = max(0, int(capacity * BACKGROUND_MAX_FRACTION))
            if capacity > 1:
                background_limit = min(background_limit, capacity - 1)
            for task in candidates:
                if claimed_count >= free:
                    break
                if _is_background(task):
                    if high_priority_waiting:
                        continue
                    if active_background >= background_limit:
                        continue
                if self._claim(neo, task, projection):
                    claimed_count += 1
                    if _is_background(task):
                        active_background += 1
        finally:
            neo.close()
        return {
            "executed": True,
            "claimed": claimed_count,
            "active": active + claimed_count,
            "capacity": capacity,
            "projection_generation": projection.generation,
            "background_limit": background_limit,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = {key: dict(value) for key, value in self._active.items()}
        return {
            "enabled": not self._stop.is_set(),
            "agent_id": EXECUTOR_AGENT_ID,
            "active": active,
            "active_count": len(active),
            "projection": self._routing.snapshot(),
            "unsafe_shell_enabled": False,
        }

    def get_nodes(self) -> list[dict[str, Any]]:
        projection = self._routing.snapshot()
        return list(projection.get("providers") or [])

    def run_once_for_testing(self) -> dict[str, Any]:
        return self.run_once()

    def stop(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)


def _start_executor_loop() -> None:
    """Start one fleet-wide executor leader through a durable Neo4j lease."""

    global _fleet_executor_instance, _executor_started
    enabled = os.getenv("ASSISTX_FLEET_EXECUTOR_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        logger.info("fleet executor: disabled by ASSISTX_FLEET_EXECUTOR_ENABLED")
        return
    with _executor_start_lock:
        if _executor_started:
            return
        _executor_started = True
        executor = FleetExecutor()
        _fleet_executor_instance = executor

        def store_factory() -> tuple[Neo4jControllerStore, Callable[[], None]]:
            neo = Neo4jClient()
            return Neo4jControllerStore(neo), neo.close

        controller = DurableController(
            "safe-fleet-executor",
            store_factory,
            lease_seconds=max(60, EXECUTOR_INTERVAL * 6),
        )
        start_durable_controller_loop(
            controller,
            executor.run_once,
            interval_seconds=EXECUTOR_INTERVAL,
        )
        logger.info("fleet executor: durable claim-fenced loop started")


def get_fleet_executor() -> Optional[FleetExecutor]:
    return _fleet_executor_instance
