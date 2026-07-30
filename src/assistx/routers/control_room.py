from __future__ import annotations

import os
import pathlib
import shutil
import socket
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


ROOT = pathlib.Path(__file__).resolve().parents[3]
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _probe_http(url: str, timeout: float = 1.5) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=timeout)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "status": "healthy" if response.status_code < 500 else "degraded",
            "latency_ms": latency_ms,
            "http_status": response.status_code,
            "detail": "reachable",
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "latency_ms": None,
            "detail": str(exc)[:180],
        }


def _probe_tcp(uri: str, timeout: float = 1.0) -> dict[str, Any]:
    parsed = urlparse(uri)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return {"status": "unknown", "detail": "invalid or missing endpoint"}
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {
            "status": "healthy",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"{host}:{port}",
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "latency_ms": None,
            "detail": str(exc)[:180],
        }


def _filesystem_dependency(name: str, path: str, required: bool) -> dict[str, Any]:
    target = pathlib.Path(path)
    exists = target.exists()
    payload: dict[str, Any] = {
        "id": name,
        "kind": "filesystem",
        "required": required,
        "status": "healthy" if exists else ("unavailable" if required else "disabled"),
        "detail": path,
    }
    if exists:
        try:
            usage = shutil.disk_usage(target)
            payload.update(
                {
                    "free_bytes": usage.free,
                    "total_bytes": usage.total,
                    "free_percent": round((usage.free / usage.total) * 100, 1)
                    if usage.total
                    else None,
                }
            )
        except OSError as exc:
            payload["status"] = "degraded"
            payload["detail"] = f"{path}: {exc}"
    return payload


def _credential_state(name: str) -> dict[str, Any]:
    configured = bool(os.getenv(name, "").strip())
    return {
        "id": name.lower(),
        "kind": "credential",
        "required": False,
        "status": "blocked" if configured else "disabled",
        "detail": "present in environment" if configured else "not configured",
    }


def build_control_room_router(
    neo_factory: Callable[[], Any],
    *,
    auth_dependency: Callable[..., Any],
) -> APIRouter:
    router = APIRouter()

    @router.get("/control-room", response_class=HTMLResponse)
    def control_room(request: Request, _: str = Depends(auth_dependency)):
        return templates.TemplateResponse("control_room.html", {"request": request})

    @router.get("/api/control-room/dependencies")
    def dependencies(_: str = Depends(auth_dependency)) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        neo4j_uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        neo4j = _probe_tcp(neo4j_uri)
        neo4j.update({"id": "neo4j", "kind": "datastore", "required": True})
        checks.append(neo4j)

        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        redis = _probe_tcp(redis_url)
        redis.update({"id": "redis", "kind": "datastore", "required": True})
        checks.append(redis)

        router_url = os.getenv("AUTO_ROUTER_BASE_URL", "").rstrip("/")
        if router_url:
            auto_router = _probe_http(f"{router_url}/health")
            auto_router.update(
                {"id": "auto-router", "kind": "control-plane", "required": True}
            )
        else:
            auto_router = {
                "id": "auto-router",
                "kind": "control-plane",
                "required": True,
                "status": "unavailable",
                "detail": "AUTO_ROUTER_BASE_URL is not configured",
            }
        checks.append(auto_router)

        admission: dict[str, Any] = {
            "id": "runtime-admission",
            "kind": "control-plane",
            "required": True,
            "status": "unknown",
            "detail": "router admission endpoint not queried",
        }
        admin_token = os.getenv("AUTO_ROUTER_ADMIN_TOKEN", "").strip()
        if router_url and admin_token:
            try:
                response = requests.get(
                    f"{router_url}/admin/admission",
                    headers={"X-Admin-Token": admin_token},
                    timeout=2.0,
                )
                response.raise_for_status()
                payload = response.json()
                runtimes = payload.get("runtimes") or []
                paths = payload.get("access_paths") or []
                admission.update(
                    {
                        "status": "healthy" if runtimes else "degraded",
                        "detail": f"{len(runtimes)} runtime(s), {len(paths)} path set(s)",
                        "runtimes": runtimes,
                        "access_paths": paths,
                    }
                )
            except Exception as exc:
                admission.update({"status": "unavailable", "detail": str(exc)[:180]})
        elif not admin_token:
            admission["detail"] = "AUTO_ROUTER_ADMIN_TOKEN is not available to AssistX"
        checks.append(admission)

        try:
            neo = neo_factory()
            try:
                with neo._session() as session:
                    session.run("RETURN 1 AS ok").consume()
                graph = {
                    "id": "assistx-graph-authority",
                    "kind": "authority",
                    "required": True,
                    "status": "healthy",
                    "detail": "Neo4j query succeeded",
                }
            finally:
                neo.close()
        except Exception as exc:
            graph = {
                "id": "assistx-graph-authority",
                "kind": "authority",
                "required": True,
                "status": "unavailable",
                "detail": str(exc)[:180],
            }
        checks.append(graph)

        checks.extend(
            [
                _filesystem_dependency("ssd-workspace", "/media/scott/SSD_4TB", False),
                _filesystem_dependency("nas-workspace", "/media/scott/NAS5", False),
                _filesystem_dependency("lms-state", "/home/scott/git/lms", False),
                _filesystem_dependency("opencode-cli", "/home/scott/.opencode/bin/opencode", False),
                _filesystem_dependency("hermes-mcp", "/home/scott/.hermes/mcp", False),
            ]
        )

        paperclip_url = os.getenv("PAPERCLIP_API_URL", "").strip()
        checks.append(
            {
                "id": "paperclip",
                "kind": "legacy-service",
                "required": False,
                "status": "configured" if paperclip_url else "disabled",
                "detail": paperclip_url or "not configured",
            }
        )
        auto_assign_url = os.getenv("AUTO_ASSIGN_BASE_URL", "").strip()
        checks.append(
            {
                "id": "auto-assign",
                "kind": "retired-service",
                "required": False,
                "status": "blocked" if auto_assign_url else "disabled",
                "detail": auto_assign_url or "retired",
            }
        )

        for key in (
            "OPENROUTER_API_KEY",
            "GROQ_API_KEY",
            "CEREBRAS_API_KEY",
            "GEMINI_API_KEY",
            "MISTRAL_API_KEY",
            "XAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ):
            checks.append(_credential_state(key))

        required_failures = [
            item
            for item in checks
            if item.get("required") and item.get("status") not in {"healthy"}
        ]
        forbidden_present = [
            item
            for item in checks
            if item.get("kind") in {"credential", "retired-service"}
            and item.get("status") == "blocked"
        ]

        return {
            "timestamp": _now_ms(),
            "status": "blocked"
            if required_failures or forbidden_present
            else "healthy",
            "summary": {
                "total": len(checks),
                "healthy": sum(item.get("status") == "healthy" for item in checks),
                "degraded": sum(
                    item.get("status") in {"degraded", "unknown", "configured"}
                    for item in checks
                ),
                "blocked": len(required_failures) + len(forbidden_present),
            },
            "checks": checks,
        }

    return router
