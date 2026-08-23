from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .deps import load_redis_module

NeoFactory = Callable[[], Any]
AuthDependency = Callable[..., Any]

_PUBLIC_PROVIDER_KEYS = (
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLOUDFLARE_API_TOKEN",
)

_LEGACY_UI_PATHS = (
    # "/" intentionally forwards to the live view; /command-center and /fleet
    # remain consolidated into the control room. Everything else renders its
    # own page again (TemplateResponse signatures fixed 2026-08-23).
    "/",
    "/command-center",
    "/fleet",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _dependency(
    name: str,
    category: str,
    status: str,
    detail: str,
    *,
    required: bool,
    latency_ms: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "status": status,
        "detail": detail,
        "required": required,
        "latency_ms": latency_ms,
        "metadata": metadata or {},
    }


def classify_runtime_mode(runtime_kind: Any, explicit_headless: Any = None) -> str:
    if isinstance(explicit_headless, bool):
        return "HEADLESS" if explicit_headless else "LM_STUDIO"
    kind = str(runtime_kind or "").strip().lower()
    if "lm studio" in kind or "lmstudio" in kind:
        return "LM_STUDIO"
    if any(token in kind for token in ("llama.cpp", "llama-server", "vllm", "ollama", "headless")):
        return "HEADLESS"
    return "UNKNOWN"


def human_activity_title(task_title: Any, task_kind: Any, task_id: Any) -> str:
    title = str(task_title or "").strip()
    if title:
        return title
    kind = str(task_kind or "").strip().replace("_", " ")
    if kind:
        return kind[:1].upper() + kind[1:]
    identifier = str(task_id or "").strip()
    return f"Task {identifier[:8]}" if identifier else "Unidentified fleet activity"


def _json_value(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, type(default)) else default
        except json.JSONDecodeError:
            return default
    return default


def _http_json(
    base_url: str,
    path: str,
    *,
    timeout: float = 2.0,
    admin_token: str = "",
) -> tuple[dict[str, Any], float]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {"Accept": "application/json"}
    if admin_token:
        headers["X-Admin-Token"] = admin_token
    started = time.monotonic()
    request = UrlRequest(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - private URLs are operator-configured
        payload = json.loads(response.read().decode("utf-8"))
    return payload, _elapsed_ms(started)


def _probe_neo4j(neo_factory: NeoFactory) -> dict[str, Any]:
    started = time.monotonic()
    neo = None
    try:
        neo = neo_factory()
        with neo._session() as session:
            record = session.run("RETURN 1 AS ok").single()
        if not record or record.get("ok") != 1:
            raise RuntimeError("unexpected Neo4j probe response")
        return _dependency(
            "Neo4j",
            "state",
            "healthy",
            "canonical fleet state is reachable",
            required=True,
            latency_ms=_elapsed_ms(started),
        )
    except Exception as exc:
        return _dependency(
            "Neo4j",
            "state",
            "unhealthy",
            str(exc)[:240],
            required=True,
            latency_ms=_elapsed_ms(started),
        )
    finally:
        if neo is not None:
            try:
                neo.close()
            except Exception:
                pass


def _probe_redis() -> dict[str, Any]:
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    started = time.monotonic()
    try:
        redis_module = load_redis_module()
        client = redis_module.from_url(redis_url, socket_connect_timeout=1.5, socket_timeout=1.5)
        if not client.ping():
            raise RuntimeError("PING did not return PONG")
        return _dependency(
            "Redis",
            "queue",
            "healthy",
            "queue and short-lived event state are reachable",
            required=True,
            latency_ms=_elapsed_ms(started),
        )
    except Exception as exc:
        return _dependency(
            "Redis",
            "queue",
            "unhealthy",
            str(exc)[:240],
            required=True,
            latency_ms=_elapsed_ms(started),
        )


def _probe_router() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_url = os.getenv("AUTO_ROUTER_BASE_URL", "").strip()
    admin_token = os.getenv("AUTO_ROUTER_ADMIN_TOKEN", "").strip()
    if not base_url:
        dependency = _dependency(
            "auto-router",
            "inference",
            "unhealthy",
            "AUTO_ROUTER_BASE_URL is not configured",
            required=True,
        )
        return [dependency], {}

    results: list[dict[str, Any]] = []
    admission: dict[str, Any] = {}
    try:
        health, latency = _http_json(base_url, "/health")
        healthy = bool(health.get("ok", True))
        results.append(
            _dependency(
                "auto-router",
                "inference",
                "healthy" if healthy else "unhealthy",
                "strict-offline router is reachable" if healthy else "router health reported failure",
                required=True,
                latency_ms=latency,
                metadata={"service": health.get("service"), "mode": "strict-offline"},
            )
        )
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        results.append(
            _dependency(
                "auto-router",
                "inference",
                "unhealthy",
                str(exc)[:240],
                required=True,
            )
        )
        return results, admission

    if not admin_token:
        results.append(
            _dependency(
                "router admission telemetry",
                "observability",
                "unhealthy",
                "AUTO_ROUTER_ADMIN_TOKEN is missing",
                required=True,
            )
        )
        return results, admission

    try:
        admission, latency = _http_json(
            base_url,
            "/admin/admission",
            admin_token=admin_token,
        )
        results.append(
            _dependency(
                "router admission telemetry",
                "observability",
                "healthy",
                "runtime slots and selected access paths are visible",
                required=True,
                latency_ms=latency,
            )
        )
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        results.append(
            _dependency(
                "router admission telemetry",
                "observability",
                "unhealthy",
                str(exc)[:240],
                required=True,
            )
        )
    return results, admission


def _probe_tailnet() -> dict[str, Any]:
    evidence_path = pathlib.Path(
        os.getenv(
            "ASSISTX_TAILNET_EVIDENCE_PATH",
            "/app/artifacts/reconciliation-tailnet-candidates.json",
        )
    )
    tailscale_bin = shutil.which("tailscale")
    if tailscale_bin:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [tailscale_bin, "status", "--json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
            payload = json.loads(completed.stdout)
            peers = payload.get("Peer") or payload.get("peer") or {}
            online = sum(1 for peer in peers.values() if peer.get("Online", peer.get("online", False)))
            return _dependency(
                "Tailscale",
                "network",
                "healthy" if online else "degraded",
                f"tailnet daemon visible; {online} peer(s) online",
                required=True,
                latency_ms=_elapsed_ms(started),
                metadata={"online_peers": online, "source": "tailscale-cli"},
            )
        except Exception as exc:
            return _dependency(
                "Tailscale",
                "network",
                "unhealthy",
                str(exc)[:240],
                required=True,
                latency_ms=_elapsed_ms(started),
            )

    if evidence_path.exists():
        try:
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            peers = payload.get("candidates") or payload.get("nodes") or payload.get("peers") or []
            age_seconds = max(0, time.time() - evidence_path.stat().st_mtime)
            status = "healthy" if age_seconds <= 900 else "degraded"
            return _dependency(
                "Tailscale",
                "network",
                status,
                f"host evidence contains {len(peers)} candidate peer(s); age {int(age_seconds)}s",
                required=True,
                metadata={"source": str(evidence_path), "age_seconds": int(age_seconds)},
            )
        except Exception as exc:
            return _dependency(
                "Tailscale",
                "network",
                "unhealthy",
                f"tailnet evidence unreadable: {str(exc)[:180]}",
                required=True,
            )

    return _dependency(
        "Tailscale",
        "network",
        "unknown",
        "tailscale CLI and candidate evidence are not visible inside this container",
        required=True,
    )


def _probe_storage() -> list[dict[str, Any]]:
    raw = os.getenv(
        "ASSISTX_MONITORED_PATHS",
        "artifacts:/app/artifacts:required,cache:/app/.cache:required,transcriptions:/app/transcriptions:optional,hermes-home:/app/hermes-home:optional",
    )
    results: list[dict[str, Any]] = []
    for entry in (part.strip() for part in raw.split(",") if part.strip()):
        pieces = entry.split(":", 2)
        if len(pieces) != 3:
            continue
        label, path_text, requirement = pieces
        path = pathlib.Path(path_text)
        required = requirement.strip().lower() == "required"
        if not path.exists():
            results.append(
                _dependency(
                    label,
                    "storage",
                    "unhealthy" if required else "disabled",
                    f"{path} is not mounted",
                    required=required,
                    metadata={"path": str(path)},
                )
            )
            continue
        usage = shutil.disk_usage(path)
        free_gib = round(usage.free / (1024**3), 1)
        free_ratio = usage.free / usage.total if usage.total else 0.0
        status = "healthy" if free_ratio >= 0.1 else "degraded"
        results.append(
            _dependency(
                label,
                "storage",
                status,
                f"{free_gib} GiB free at {path}",
                required=required,
                metadata={
                    "path": str(path),
                    "free_gib": free_gib,
                    "free_percent": round(free_ratio * 100, 1),
                },
            )
        )
    return results


def _probe_executors() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, binary, required in (
        ("Hermes", os.getenv("HERMES_BIN", "hermes"), False),
        ("OpenCode", os.getenv("OPENCODE_BIN", "opencode"), False),
    ):
        resolved = shutil.which(binary)
        results.append(
            _dependency(
                name,
                "executor",
                "healthy" if resolved else "disabled",
                resolved or f"{binary} is not available in this container",
                required=required,
                metadata={"binary": binary, "resolved_path": resolved},
            )
        )
    return results


def _probe_policy() -> list[dict[str, Any]]:
    configured_public = [name for name in _PUBLIC_PROVIDER_KEYS if os.getenv(name, "").strip()]
    public_status = "healthy" if not configured_public else "unhealthy"
    public_detail = (
        "public inference credentials are absent"
        if not configured_public
        else f"forbidden credentials configured: {', '.join(configured_public)}"
    )
    auto_assign = os.getenv("AUTO_ASSIGN_BASE_URL", "").strip()
    paperclip = os.getenv("PAPERCLIP_API_URL", "").strip()
    egress_mode = os.getenv("ASSISTX_TOOL_EGRESS_MODE", "disabled").strip().lower()
    return [
        _dependency(
            "Public inference",
            "policy",
            public_status,
            public_detail,
            required=True,
        ),
        _dependency(
            "auto-assign",
            "policy",
            "healthy" if not auto_assign else "unhealthy",
            "retired and unconfigured" if not auto_assign else f"legacy endpoint still configured: {auto_assign}",
            required=True,
        ),
        _dependency(
            "Paperclip",
            "integration",
            "disabled" if not paperclip else "degraded",
            "not configured" if not paperclip else f"legacy integration configured at {paperclip}",
            required=False,
        ),
        _dependency(
            "Tool web egress",
            "policy",
            "healthy" if egress_mode in {"disabled", "allowlisted"} else "degraded",
            f"mode: {egress_mode or 'unspecified'}",
            required=True,
            metadata={"mode": egress_mode or "unspecified"},
        ),
    ]


def collect_dependencies(neo_factory: NeoFactory) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    router_dependencies, admission = _probe_router()
    dependencies = [
        _probe_neo4j(neo_factory),
        _probe_redis(),
        *router_dependencies,
        _probe_tailnet(),
        *_probe_storage(),
        *_probe_executors(),
        *_probe_policy(),
    ]
    return dependencies, admission


def _query_rows(neo_factory: NeoFactory, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    neo = neo_factory()
    try:
        with neo._session() as session:
            return [dict(row) for row in session.run(query, parameters or {})]
    finally:
        neo.close()


def _endpoint_inventory(neo_factory: NeoFactory) -> list[dict[str, Any]]:
    try:
        return _query_rows(
            neo_factory,
            """
            MATCH (m:ModelEndpoint)
            RETURN m.node_id AS node_id,
                   m.runtime_instance_id AS runtime_instance_id,
                   m.base_url AS base_url,
                   m.status AS status,
                   m.models_json AS models_json,
                   m.runtime_kind AS runtime_kind,
                   m.runtime_version AS runtime_version,
                   m.headless AS headless,
                   m.quantization AS quantization,
                   m.context_length AS context_length,
                   m.last_seen AS last_seen,
                   m.last_seen_ts AS last_seen_ts,
                   m.process_id AS process_id,
                   m.load_owner AS load_owner
            ORDER BY m.node_id
            """,
        )
    except Exception:
        return []


def _runtime_inventory(neo_factory: NeoFactory) -> list[dict[str, Any]]:
    try:
        return _query_rows(
            neo_factory,
            """
            MATCH (r:RuntimeInstance)
            OPTIONAL MATCH (r)-[:SERVES]->(m:LoadedModelInstance)
            RETURN r.runtime_instance_id AS runtime_instance_id,
                   r.node_id AS node_id,
                   r.runtime_kind AS runtime_kind,
                   r.runtime_version AS runtime_version,
                   r.process_id AS process_id,
                   r.service_manager AS service_manager,
                   r.headless AS headless,
                   r.status AS status,
                   r.last_seen_ts AS last_seen_ts,
                   collect({
                       model_instance_id: m.model_instance_id,
                       model_key: m.model_key,
                       quantization: m.quantization,
                       context_length: m.context_length,
                       loaded_at_ts: m.loaded_at_ts,
                       load_owner: m.load_owner
                   }) AS loaded_models
            ORDER BY r.node_id, r.runtime_instance_id
            """,
        )
    except Exception:
        return []


def collect_runtimes(neo_factory: NeoFactory, admission: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint_rows = _endpoint_inventory(neo_factory)
    runtime_rows = _runtime_inventory(neo_factory)
    runtime_map: dict[str, dict[str, Any]] = {}

    for row in runtime_rows:
        runtime_id = str(row.get("runtime_instance_id") or row.get("node_id") or "unknown")
        models = [model for model in (row.get("loaded_models") or []) if model.get("model_key")]
        runtime_map[runtime_id] = {
            **row,
            "runtime_instance_id": runtime_id,
            "loaded_models": models,
            "access_paths": [],
            "parallel_slots": 0,
            "active": 0,
            "queued": 0,
        }

    for row in endpoint_rows:
        runtime_id = str(row.get("runtime_instance_id") or row.get("node_id") or "unknown")
        models_raw = _json_value(row.get("models_json"), [])
        models = []
        for model in models_raw:
            if isinstance(model, str):
                models.append({"model_key": model})
            elif isinstance(model, dict):
                models.append(model)
        target = runtime_map.setdefault(
            runtime_id,
            {
                "runtime_instance_id": runtime_id,
                "node_id": row.get("node_id"),
                "loaded_models": [],
                "access_paths": [],
                "parallel_slots": 0,
                "active": 0,
                "queued": 0,
            },
        )
        for key in (
            "node_id",
            "runtime_kind",
            "runtime_version",
            "process_id",
            "headless",
            "status",
            "last_seen_ts",
            "load_owner",
        ):
            if target.get(key) in (None, "") and row.get(key) not in (None, ""):
                target[key] = row.get(key)
        if row.get("base_url") and row.get("base_url") not in target["access_paths"]:
            target["access_paths"].append(row.get("base_url"))
        if models and not target["loaded_models"]:
            target["loaded_models"] = models

    access_by_runtime = {
        str(item.get("runtime_instance_id")): item
        for item in admission.get("access_paths") or []
    }
    slots_by_runtime = {
        str(item.get("runtime_instance_id")): item
        for item in admission.get("runtimes") or []
    }
    for runtime_id in set(runtime_map) | set(access_by_runtime) | set(slots_by_runtime):
        target = runtime_map.setdefault(
            runtime_id,
            {
                "runtime_instance_id": runtime_id,
                "node_id": None,
                "loaded_models": [],
                "access_paths": [],
            },
        )
        access = access_by_runtime.get(runtime_id, {})
        slots = slots_by_runtime.get(runtime_id, {})
        target["access_paths"] = access.get("approved_access_urls") or target.get("access_paths") or []
        target["selected_access_url"] = access.get("selected_access_url")
        target["selected_transport"] = access.get("selected_transport")
        target["path_selection_fresh"] = access.get("selection_fresh")
        target["probe_failures"] = access.get("probe_failures") or {}
        target["parallel_slots"] = int(slots.get("parallel_slots") or 0)
        target["active"] = int(slots.get("active") or 0)
        target["queued"] = int(slots.get("queued") or 0)
        target["queue_limit"] = int(slots.get("queue_limit") or 0)
        target["runtime_mode"] = classify_runtime_mode(target.get("runtime_kind"), target.get("headless"))
        target["status"] = target.get("status") or (
            "online" if target.get("path_selection_fresh") else "unknown"
        )

    return sorted(
        runtime_map.values(),
        key=lambda item: (str(item.get("node_id") or "~"), str(item.get("runtime_instance_id"))),
    )


def collect_activity(neo_factory: NeoFactory, *, limit: int = 100) -> list[dict[str, Any]]:
    cutoff = _now_ms() - 24 * 60 * 60 * 1000
    try:
        rows = _query_rows(
            neo_factory,
            """
            MATCH (r:AgentRun)
            WHERE coalesce(r.created_at_ts, 0) >= $cutoff
            OPTIONAL MATCH (t:Task {id: r.task_id})
            RETURN r.id AS run_id,
                   r.task_id AS task_id,
                   t.title AS task_title,
                   coalesce(t.kind, t.task_type, r.task_kind) AS task_kind,
                   t.repository AS repository,
                   t.payload_json AS task_payload_json,
                   r.status AS status,
                   r.agent AS agent,
                   r.model AS model,
                   r.runtime_node_id AS runtime_node_id,
                   r.runtime_instance_id AS runtime_instance_id,
                   r.runtime_kind AS runtime_kind,
                   r.selected_transport AS selected_transport,
                   r.selected_access_url AS selected_access_url,
                   r.stage AS stage,
                   r.queue_wait_ms AS queue_wait_ms,
                   coalesce(r.time_to_first_token_ms, r.ttft_ms) AS ttft_ms,
                   coalesce(r.tokens_per_second, r.tps) AS tokens_per_second,
                   r.prompt_tokens AS prompt_tokens,
                   r.completion_tokens AS completion_tokens,
                   r.started_at_ts AS started_at_ts,
                   r.ended_at_ts AS ended_at_ts,
                   r.created_at_ts AS created_at_ts,
                   substring(coalesce(r.result_json, '{}'), 0, 500) AS result_preview,
                   r.error_class AS error_class
            ORDER BY r.created_at_ts DESC
            LIMIT $limit
            """,
            {"cutoff": cutoff, "limit": limit},
        )
    except Exception:
        return []

    activity: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_value(row.get("task_payload_json"), {})
        repository = row.get("repository") or payload.get("repository") or payload.get("repo")
        title = human_activity_title(row.get("task_title"), row.get("task_kind"), row.get("task_id"))
        started = row.get("started_at_ts")
        ended = row.get("ended_at_ts")
        duration_ms = max(0, int(ended) - int(started)) if started and ended else None
        activity.append(
            {
                **row,
                "display_title": title,
                "repository": repository,
                "stage": row.get("stage") or payload.get("stage") or payload.get("execution_stage"),
                "duration_ms": duration_ms,
                "runtime_mode": classify_runtime_mode(row.get("runtime_kind")),
            }
        )
    return activity


def collect_performance(neo_factory: NeoFactory) -> list[dict[str, Any]]:
    try:
        return _query_rows(
            neo_factory,
            """
            MATCH (p:ModelPerf)
            RETURN coalesce(p.node_id, p.hostname, 'unknown') AS node_id,
                   coalesce(p.model, p.model_id, 'unknown') AS model,
                   count(p) AS runs,
                   round(avg(coalesce(p.tps, 0.0)) * 100) / 100 AS tps_avg,
                   round(avg(coalesce(p.latency_ms, 0.0)) * 10) / 10 AS latency_ms_avg,
                   round(avg(coalesce(p.ttft_ms, 0.0)) * 10) / 10 AS ttft_ms_avg,
                   round(avg(coalesce(p.quality_score, p.eval_score, 0.0)) * 1000) / 1000 AS quality_avg,
                   round(avg(CASE WHEN coalesce(p.ok, true) THEN 0.0 ELSE 1.0 END) * 10000) / 100 AS error_percent
            ORDER BY runs DESC
            LIMIT 100
            """,
        )
    except Exception:
        return []


def build_overview(neo_factory: NeoFactory) -> dict[str, Any]:
    collected_at = _now_ms()
    dependencies, admission = collect_dependencies(neo_factory)
    runtimes = collect_runtimes(neo_factory, admission)
    activity = collect_activity(neo_factory)
    performance = collect_performance(neo_factory)
    required_failures = [
        item for item in dependencies
        if item.get("required") and item.get("status") not in {"healthy", "disabled"}
    ]
    active = sum(int(runtime.get("active") or 0) for runtime in runtimes)
    queued = sum(int(runtime.get("queued") or 0) for runtime in runtimes)
    slots = sum(int(runtime.get("parallel_slots") or 0) for runtime in runtimes)
    loaded_models = sum(len(runtime.get("loaded_models") or []) for runtime in runtimes)
    weighted_tps_runs = sum(float(row.get("tps_avg") or 0) * int(row.get("runs") or 0) for row in performance)
    performance_runs = sum(int(row.get("runs") or 0) for row in performance)
    average_tps = round(weighted_tps_runs / performance_runs, 2) if performance_runs else None
    error_weight = sum(float(row.get("error_percent") or 0) * int(row.get("runs") or 0) for row in performance)
    error_percent = round(error_weight / performance_runs, 2) if performance_runs else None

    return {
        "collected_at_ts": collected_at,
        "overall_status": "healthy" if not required_failures else "degraded",
        "summary": {
            "runtime_count": len(runtimes),
            "healthy_runtime_count": sum(1 for item in runtimes if item.get("status") in {"online", "healthy"}),
            "loaded_model_count": loaded_models,
            "active_requests": active,
            "queued_requests": queued,
            "parallel_slots": slots,
            "available_slots": max(0, slots - active),
            "average_tokens_per_second": average_tps,
            "error_percent": error_percent,
            "activity_count": len(activity),
            "required_dependency_failures": len(required_failures),
        },
        "dependencies": dependencies,
        "runtimes": runtimes,
        "activity": activity,
        "performance": performance,
        "admission": admission,
    }


def build_control_room_router(
    neo_factory: NeoFactory,
    auth_dependency: AuthDependency,
    templates: Jinja2Templates,
) -> APIRouter:
    router = APIRouter(tags=["control-room"])

    @router.get("/control-room", response_class=HTMLResponse)
    def control_room(request: Request, _: str = Depends(auth_dependency)) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="control_room.html")

    @router.get("/api/control-room/overview")
    def control_room_overview(_: str = Depends(auth_dependency)) -> dict[str, Any]:
        return build_overview(neo_factory)

    @router.get("/api/control-room/stream")
    async def control_room_stream(_: str = Depends(auth_dependency)) -> StreamingResponse:
        async def event_stream():
            while True:
                try:
                    payload = await asyncio.to_thread(build_overview, neo_factory)
                    serialized = json.dumps(payload, separators=(",", ":"), default=str)
                    yield f"event: snapshot\ndata: {serialized}\n\n"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error = json.dumps({"error": str(exc)[:240], "collected_at_ts": _now_ms()})
                    yield f"event: error\ndata: {error}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    def redirect_to_control_room() -> RedirectResponse:
        return RedirectResponse(url="/control-room", status_code=307)

    for path in _LEGACY_UI_PATHS:
        router.add_api_route(
            path,
            redirect_to_control_room,
            methods=["GET"],
            include_in_schema=False,
        )

    return router


LEGACY_UI_PATHS = frozenset(_LEGACY_UI_PATHS)
