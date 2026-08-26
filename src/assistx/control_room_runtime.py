from __future__ import annotations

import copy
import json
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any


_CACHE_LOCK = threading.Lock()
_CACHE_VALUE: dict[str, Any] | None = None
_CACHE_EXPIRES_AT = 0.0


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _query(neo_factory: Callable[[], Any], query: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    neo = neo_factory()
    try:
        with neo._session() as session:
            return [dict(row) for row in session.run(query, parameters)]
    finally:
        neo.close()


def collect_router_telemetry(
    neo_factory: Callable[[], Any],
    *,
    limit: int = 1000,
    window_hours: int = 24,
) -> dict[str, Any]:
    cutoff = int(time.time() * 1000) - max(1, window_hours) * 60 * 60 * 1000
    try:
        rows = _query(
            neo_factory,
            """
            MATCH (e:EventEnvelope)
            WHERE e.event_type STARTS WITH 'router.execution_stage.'
              AND coalesce(e.created_at_ts, 0) >= $cutoff
            RETURN e.event_id AS event_id,
                   e.event_type AS event_type,
                   e.correlation_id AS correlation_id,
                   e.node_id AS envelope_node_id,
                   e.payload_json AS payload_json,
                   e.created_at_ts AS created_at_ts
            ORDER BY e.created_at_ts DESC
            LIMIT $limit
            """,
            {"cutoff": cutoff, "limit": int(limit)},
        )
    except Exception:
        return {"activity": [], "performance": [], "runtime_samples": [], "event_count": 0}

    payloads: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for row in rows:
        payload = _json_dict(row.get("payload_json"))
        payload["event_id"] = row.get("event_id")
        payload["event_type"] = row.get("event_type")
        payload["correlation_id"] = payload.get("correlation_id") or row.get("correlation_id")
        payload["created_at_ts"] = payload.get("ended_at_ms") or payload.get("started_at_ms") or row.get("created_at_ts")
        payload["runtime_node_id"] = payload.get("runtime_node_id") or payload.get("node_id") or row.get("envelope_node_id")
        task_id = str(payload.get("task_id") or "").strip()
        if task_id:
            task_ids.add(task_id)
        payloads.append(payload)

    task_map: dict[str, dict[str, Any]] = {}
    if task_ids:
        try:
            task_rows = _query(
                neo_factory,
                """
                MATCH (t:Task)
                WHERE coalesce(t.id, t.task_id) IN $task_ids
                RETURN coalesce(t.id, t.task_id) AS task_id,
                       t.title AS title,
                       coalesce(t.kind, t.task_type) AS task_kind,
                       t.repository AS repository,
                       t.payload_json AS payload_json
                """,
                {"task_ids": sorted(task_ids)},
            )
            task_map = {str(row.get("task_id")): row for row in task_rows}
        except Exception:
            task_map = {}

    activity: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    latest_by_runtime: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        task_id = str(payload.get("task_id") or "")
        task = task_map.get(task_id, {})
        task_payload = _json_dict(task.get("payload_json"))
        status = str(payload.get("status") or "UNKNOWN").upper()
        started = payload.get("started_at_ms")
        ended = payload.get("ended_at_ms")
        duration_ms = None
        if isinstance(started, (int, float)) and isinstance(ended, (int, float)):
            duration_ms = max(0, int(ended) - int(started))
        title = str(task.get("title") or payload.get("task_title") or "").strip()
        if not title:
            kind = str(task.get("task_kind") or payload.get("task_kind") or "").replace("_", " ").strip()
            title = kind[:1].upper() + kind[1:] if kind else f"Inference request {str(payload.get('request_id') or payload.get('event_id') or '')[:8]}"
        model = str(payload.get("model_key") or payload.get("provider_model_id") or payload.get("model") or "unknown")
        node_id = str(payload.get("runtime_node_id") or payload.get("node_id") or "unknown")
        activity.append(
            {
                "display_title": title,
                "task_id": task_id or None,
                "run_id": payload.get("agent_run_id") or payload.get("request_id") or payload.get("event_id"),
                "request_id": payload.get("request_id"),
                "correlation_id": payload.get("correlation_id"),
                "status": status,
                "agent": payload.get("agent") or payload.get("executor") or "router",
                "model": model,
                "repository": task.get("repository") or task_payload.get("repository") or task_payload.get("repo"),
                "task_kind": task.get("task_kind") or payload.get("task_kind"),
                "stage": payload.get("stage") or task_payload.get("stage") or task_payload.get("execution_stage"),
                "runtime_node_id": node_id,
                "runtime_instance_id": payload.get("runtime_instance_id"),
                "runtime_kind": payload.get("runtime_kind"),
                "runtime_version": payload.get("runtime_version"),
                "selected_transport": payload.get("selected_transport"),
                "selected_access_url": payload.get("selected_access_url"),
                "queue_wait_ms": payload.get("queue_wait_ms"),
                "ttft_ms": payload.get("time_to_first_token_ms") or payload.get("ttft_ms") or payload.get("load_time_ms"),
                "tokens_per_second": payload.get("tokens_per_second"),
                "prompt_tokens": payload.get("input_tokens") or payload.get("prompt_tokens"),
                "completion_tokens": payload.get("output_tokens") or payload.get("completion_tokens"),
                "duration_ms": duration_ms,
                "created_at_ts": payload.get("created_at_ts"),
                "error_class": payload.get("error_type") or payload.get("error_class"),
                "result_preview": payload.get("error_message") or "",
            }
        )
        groups[(node_id, model)].append(payload)
        runtime_id = str(payload.get("runtime_instance_id") or "").strip()
        if runtime_id and runtime_id not in latest_by_runtime:
            latest_by_runtime[runtime_id] = payload

    performance: list[dict[str, Any]] = []
    for (node_id, model), samples in groups.items():
        def average(key: str, *fallbacks: str) -> float | None:
            values = []
            for sample in samples:
                value = sample.get(key)
                if value is None:
                    for fallback in fallbacks:
                        value = sample.get(fallback)
                        if value is not None:
                            break
                if isinstance(value, (int, float)):
                    values.append(float(value))
            return round(sum(values) / len(values), 3) if values else None

        failures = sum(1 for sample in samples if str(sample.get("status") or "").lower() != "completed")
        performance.append(
            {
                "node_id": node_id,
                "model": model,
                "runs": len(samples),
                "tps_avg": average("tokens_per_second"),
                "latency_ms_avg": average("latency_ms"),
                "ttft_ms_avg": average("time_to_first_token_ms", "ttft_ms", "load_time_ms"),
                "quality_avg": average("quality_score", "eval_score"),
                "error_percent": round(failures / len(samples) * 100, 2),
            }
        )
    performance.sort(key=lambda item: (-int(item.get("runs") or 0), str(item.get("model"))))

    runtime_samples = []
    for runtime_id, payload in latest_by_runtime.items():
        runtime_samples.append(
            {
                "runtime_instance_id": runtime_id,
                "node_id": payload.get("runtime_node_id") or payload.get("node_id"),
                "runtime_kind": payload.get("runtime_kind"),
                "runtime_version": payload.get("runtime_version"),
                "selected_transport": payload.get("selected_transport"),
                "selected_access_url": payload.get("selected_access_url"),
                "last_request_id": payload.get("request_id"),
                "last_seen_ts": payload.get("created_at_ts"),
                "tokens_per_second": payload.get("tokens_per_second"),
                "ttft_ms": payload.get("time_to_first_token_ms") or payload.get("ttft_ms") or payload.get("load_time_ms"),
                "model_key": payload.get("model_key") or payload.get("provider_model_id") or payload.get("model"),
                "quantization": payload.get("quantization"),
                "context_length": payload.get("context_length"),
            }
        )
    return {
        "activity": activity,
        "performance": performance,
        "runtime_samples": runtime_samples,
        "event_count": len(payloads),
    }


def _merge(base: dict[str, Any], telemetry: dict[str, Any]) -> dict[str, Any]:
    activity = telemetry.get("activity") or []
    if activity:
        known = {str(item.get("run_id") or item.get("request_id") or "") for item in activity}
        base["activity"] = activity + [
            item for item in base.get("activity") or []
            if str(item.get("run_id") or item.get("request_id") or "") not in known
        ]
    performance = telemetry.get("performance") or []
    if performance:
        known_perf = {(str(item.get("node_id")), str(item.get("model"))) for item in performance}
        base["performance"] = performance + [
            item for item in base.get("performance") or []
            if (str(item.get("node_id")), str(item.get("model"))) not in known_perf
        ]

    runtime_map = {str(item.get("runtime_instance_id")): item for item in base.get("runtimes") or []}
    node_map = {
        str(item.get("node_id")): item
        for item in base.get("runtimes") or []
        if item.get("node_id")
    }
    for sample in telemetry.get("runtime_samples") or []:
        runtime_id = str(sample.get("runtime_instance_id") or "")
        if not runtime_id:
            continue
        sample_node = str(sample.get("node_id") or "")
        existing = runtime_map.get(runtime_id) or (
            node_map.get(sample_node) if sample_node else None
        )
        if existing is not None:
            # Fold the sample into the node's canonical row — old samples carry
            # stale instance ids and must not resurrect duplicate rows.
            runtime = existing
        else:
            runtime = runtime_map.setdefault(
                runtime_id,
                {
                    "runtime_instance_id": runtime_id,
                    "node_id": sample.get("node_id"),
                    "loaded_models": [],
                    "access_paths": [],
                    "parallel_slots": 0,
                    "active": 0,
                    "queued": 0,
                    "queue_limit": 0,
                    "status": "unknown",
                    "runtime_mode": "UNKNOWN",
                },
            )
            if sample_node and not runtime.get("node_id"):
                runtime["node_id"] = sample_node
        for key, value in sample.items():
            if value is not None and key != "runtime_instance_id":
                runtime[key] = value
        model_key = sample.get("model_key")
        if model_key and not runtime.get("loaded_models"):
            runtime["loaded_models"] = [
                {
                    "model_key": model_key,
                    "quantization": sample.get("quantization"),
                    "context_length": sample.get("context_length"),
                }
            ]
    base["runtimes"] = sorted(runtime_map.values(), key=lambda item: (str(item.get("node_id") or "~"), str(item.get("runtime_instance_id"))))

    performance_rows = base.get("performance") or []
    weighted_tps = sum(float(row.get("tps_avg") or 0) * int(row.get("runs") or 0) for row in performance_rows)
    runs = sum(int(row.get("runs") or 0) for row in performance_rows)
    weighted_errors = sum(float(row.get("error_percent") or 0) * int(row.get("runs") or 0) for row in performance_rows)
    summary = base.setdefault("summary", {})
    if runs:
        summary["average_tokens_per_second"] = round(weighted_tps / runs, 2)
        summary["error_percent"] = round(weighted_errors / runs, 2)
    summary["activity_count"] = len(base.get("activity") or [])
    summary["runtime_count"] = len(base.get("runtimes") or [])
    summary["healthy_runtime_count"] = sum(1 for item in base.get("runtimes") or [] if item.get("status") in {"online", "healthy"})
    base["telemetry"] = {
        "router_event_count": telemetry.get("event_count", 0),
        "runtime_sample_count": len(telemetry.get("runtime_samples") or []),
        "source": "router.execution_stage EventEnvelope",
    }
    return base


def install_control_room_runtime(control_room_module: Any) -> None:
    original = control_room_module.build_overview
    if getattr(original, "_assistx_cached_runtime", False):
        return

    def cached_build_overview(neo_factory: Callable[[], Any]) -> dict[str, Any]:
        global _CACHE_VALUE, _CACHE_EXPIRES_AT
        ttl = max(float(os.getenv("ASSISTX_CONTROL_ROOM_CACHE_SECONDS", "3")), 1.0)
        now = time.monotonic()
        with _CACHE_LOCK:
            if _CACHE_VALUE is not None and now < _CACHE_EXPIRES_AT:
                return copy.deepcopy(_CACHE_VALUE)
            base = original(neo_factory)
            telemetry = collect_router_telemetry(neo_factory)
            value = _merge(base, telemetry)
            _CACHE_VALUE = copy.deepcopy(value)
            _CACHE_EXPIRES_AT = time.monotonic() + ttl
            return value

    cached_build_overview._assistx_cached_runtime = True  # type: ignore[attr-defined]
    control_room_module.build_overview = cached_build_overview
