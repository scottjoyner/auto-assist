from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict
from urllib.parse import urlparse
from typing import Any, Dict, List

import requests


_ALLOWED_MODES = {"direct", "router", "router_plus_assign"}


def overlay_mode() -> str:
    value = os.getenv("ASSISTX_OVERLAY_MODE", "direct").strip().lower() or "direct"
    if value == "overlay":
        return "router_plus_assign"
    return value if value in _ALLOWED_MODES else "direct"


def _env_present(name: str) -> bool:
    return name in os.environ and bool(os.environ[name].strip())


def _clean_url(value: str) -> str:
    return value.strip().rstrip("/")


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _default_health_path(value: str, default: str = "/health") -> str:
    cleaned = value.strip()
    return cleaned if cleaned else default


@dataclass(frozen=True)
class OverlayServiceConfig:
    name: str
    base_url: str
    health_path: str = "/health"
    required: bool = False

    @property
    def health_url(self) -> str:
        return f"{_clean_url(self.base_url)}{self.health_path}"


def build_overlay_configuration() -> Dict[str, Any]:
    mode = overlay_mode()
    router_url = os.getenv("AUTO_ROUTER_BASE_URL", "").strip()
    assign_url = os.getenv("AUTO_ASSIGN_BASE_URL", "").strip()
    router_health_path = _default_health_path(os.getenv("AUTO_ROUTER_HEALTH_PATH", "/health"))
    assign_health_path = _default_health_path(os.getenv("AUTO_ASSIGN_HEALTH_PATH", "/health"))

    issues: List[Dict[str, Any]] = []

    def add_issue(field: str, reason: str) -> None:
        issues.append({"field": field, "reason": reason})

    if mode not in _ALLOWED_MODES:
        add_issue("ASSISTX_OVERLAY_MODE", f"unsupported mode '{mode}'")
        mode = "direct"

    overlay_enabled = mode != "direct"

    if overlay_enabled and mode in {"router", "router_plus_assign"}:
        if not router_url:
            add_issue("AUTO_ROUTER_BASE_URL", "required when overlay mode uses auto-router")
        elif not _is_http_url(router_url):
            add_issue("AUTO_ROUTER_BASE_URL", "must be an http(s) URL")

    if overlay_enabled and mode == "router_plus_assign":
        if not assign_url:
            add_issue("AUTO_ASSIGN_BASE_URL", "required when overlay mode uses auto-assign")
        elif not _is_http_url(assign_url):
            add_issue("AUTO_ASSIGN_BASE_URL", "must be an http(s) URL")

    services = {
        "auto_router": {
            "enabled": overlay_enabled and bool(router_url),
            "required": overlay_enabled and mode in {"router", "router_plus_assign"},
            "base_url": router_url or None,
            "health_path": router_health_path,
            "health_url": f"{_clean_url(router_url)}{router_health_path}" if router_url else None,
        },
        "auto_assign": {
            "enabled": overlay_enabled and bool(assign_url),
            "required": overlay_enabled and mode == "router_plus_assign",
            "base_url": assign_url or None,
            "health_path": assign_health_path,
            "health_url": f"{_clean_url(assign_url)}{assign_health_path}" if assign_url else None,
        },
    }

    ok = not issues
    return {
        "ok": ok,
        "status": "ok" if ok else "degraded",
        "mode": mode,
        "issues": issues,
        "services": services,
    }


def validate_overlay_configuration(*, strict: bool = False) -> Dict[str, Any]:
    config = build_overlay_configuration()
    if strict and config["mode"] != "direct" and not config["ok"]:
        problems = "; ".join(f"{item['field']}: {item['reason']}" for item in config["issues"])
        raise RuntimeError(f"invalid overlay configuration: {problems}")
    return config


def _check_overlay_service(service: OverlayServiceConfig) -> Dict[str, Any]:
    if not service.base_url:
        return {
            "status": "disabled",
            "base_url": None,
            "health_url": None,
            "required": service.required,
            "enabled": False,
        }
    try:
        resp = requests.get(service.health_url, timeout=float(os.getenv("ASSISTX_OVERLAY_HEALTH_TIMEOUT_S", "3")))
        if resp.ok:
            payload: Dict[str, Any] = {
                "status": "ok",
                "base_url": service.base_url,
                "health_url": service.health_url,
                "required": service.required,
                "enabled": True,
            }
            try:
                payload["response"] = resp.json()
            except Exception:
                payload["response"] = {"text": resp.text[:500]}
            return payload
        return {
            "status": "degraded",
            "base_url": service.base_url,
            "health_url": service.health_url,
            "required": service.required,
            "enabled": True,
            "reason": f"HTTP {resp.status_code}",
        }
    except Exception as exc:
        return {
            "status": "down",
            "base_url": service.base_url,
            "health_url": service.health_url,
            "required": service.required,
            "enabled": True,
            "reason": str(exc)[:500],
        }


def build_overlay_health() -> Dict[str, Any]:
    config = build_overlay_configuration()
    services_cfg = config["services"]
    if config["mode"] == "direct":
        services = {
            "auto_router": {
                "status": "disabled",
                "base_url": None,
                "health_url": None,
                "required": False,
                "enabled": False,
            },
            "auto_assign": {
                "status": "disabled",
                "base_url": None,
                "health_url": None,
                "required": False,
                "enabled": False,
            },
        }
        return {
            "ok": True,
            "status": "ok",
            "mode": config["mode"],
            "timestamp": int(time.time() * 1000),
            "configuration": config,
            "services": services,
            "issues": [],
        }

    router_cfg = services_cfg["auto_router"]
    assign_cfg = services_cfg["auto_assign"]
    router = _check_overlay_service(
        OverlayServiceConfig(
            name="auto-router",
            base_url=router_cfg["base_url"] or "",
            health_path=router_cfg["health_path"],
            required=bool(router_cfg["required"]),
        )
    )
    assign = _check_overlay_service(
        OverlayServiceConfig(
            name="auto-assign",
            base_url=assign_cfg["base_url"] or "",
            health_path=assign_cfg["health_path"],
            required=bool(assign_cfg["required"]),
        )
    )

    services = {"auto_router": router, "auto_assign": assign}
    issues = list(config["issues"])
    for key, service in services.items():
        if service["required"] and service["status"] != "ok":
            issues.append({"field": key, "reason": service.get("reason") or "unavailable"})

    ok = not issues and all(service["status"] in {"ok", "disabled"} for service in services.values())
    return {
        "ok": ok,
        "status": "ok" if ok else "degraded",
        "mode": config["mode"],
        "timestamp": int(time.time() * 1000),
        "configuration": config,
        "services": services,
        "issues": issues,
    }


def overlay_endpoints() -> Dict[str, Any]:
    router_url = os.getenv("AUTO_ROUTER_BASE_URL", "").strip().rstrip("/")
    assign_url = os.getenv("AUTO_ASSIGN_BASE_URL", "").strip().rstrip("/")
    return {
        "mode": overlay_mode(),
        "auto_router": {
            "base_url": router_url or None,
            "health": f"{router_url}/health" if router_url else None,
            "context_projection": f"{router_url}/api/router/context-projection" if router_url else None,
            "backlog_candidates": f"{router_url}/api/router/backlog-candidates" if router_url else None,
        },
        "auto_assign": {
            "base_url": assign_url or None,
            "health": f"{assign_url}/health" if assign_url else None,
            "scheduler_tick": f"{assign_url}/api/scheduler/tick" if assign_url else None,
        },
    }
