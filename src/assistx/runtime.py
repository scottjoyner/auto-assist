from __future__ import annotations

import os
import time
from urllib.parse import urlparse
from typing import Any, Dict, List

import requests

from .deps import dependency_mode
from . import __version__

_start_time = time.time()
from .overlay import build_overlay_configuration, build_overlay_health, overlay_mode


def runtime_profile() -> str:
    value = os.getenv("ASSISTX_RUNTIME_PROFILE", dependency_mode())
    return value.strip().lower() or "production"


def _env_present(name: str) -> bool:
    return name in os.environ and bool(os.environ[name].strip())


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def build_runtime_configuration() -> Dict[str, Any]:
    profile = runtime_profile()
    dependency = dependency_mode()
    issues: List[Dict[str, Any]] = []
    fields: Dict[str, Dict[str, Any]] = {}

    def add_field(name: str, *, default: str = "", required: bool = False, validator=None, secret: bool = False) -> None:
        present = _env_present(name)
        value = os.getenv(name, default).strip()
        ok = True
        reason = None
        if required and not present:
            ok = False
            reason = "missing"
        elif value and validator is not None and not validator(value):
            ok = False
            reason = "invalid"
        fields[name] = {
            "present": present,
            "configured": present,
            "default_used": not present and bool(default),
            "value": None if secret else value,
            "required": required,
        }
        if not ok:
            issues.append({"field": name, "reason": reason or "invalid"})

    add_field("REDIS_URL", default="redis://redis:6379/0", required=profile == "production")
    add_field("NEO4J_URI", default="bolt://neo4j:7687", required=profile == "production", validator=lambda value: value.startswith(("bolt://", "neo4j://")))
    add_field("NEO4J_USER", default="neo4j", required=profile == "production")
    # No default password. Production profiles require it to be injected.
    add_field("NEO4J_PASSWORD", default="", required=profile == "production", secret=True)
    add_field("NEO4J_DATABASE", default="assistx")
    add_field("LLM_BACKEND", default="openai", required=profile == "production")
    add_field("OPENAI_BASE_URL", default="http://host.docker.internal:1234/v1", required=profile == "production", validator=_is_http_url)
    add_field("OPENAI_API_KEY", default="not-needed", secret=True)
    add_field("LLM_MODEL", default=os.getenv("OLLAMA_MODEL", "llama3.1:8b"), required=profile == "production")
    add_field("EMBED_MODEL", default=os.getenv("QA_EMBED_MODEL", "nomic-embed-text"), required=profile == "production")
    add_field("DRAFT_MODEL_BASE_URL", default="", validator=_is_http_url)
    add_field("DRAFT_MODEL_NAME", default="")
    add_field("DRAFT_MODEL_API_KEY", default="", secret=True)
    add_field("ASSISTX_OVERLAY_MODE", default="direct")
    add_field("AUTO_ROUTER_BASE_URL", default="")
    add_field("AUTO_ASSIGN_BASE_URL", default="")
    add_field("AUTO_ROUTER_HEALTH_PATH", default="/health")
    add_field("AUTO_ASSIGN_HEALTH_PATH", default="/health")

    llm_backend = fields["LLM_BACKEND"]["value"].lower() if fields["LLM_BACKEND"]["value"] else "openai"
    if profile == "production" and llm_backend != "openai":
        issues.append({"field": "LLM_BACKEND", "reason": "production requires the OpenAI-compatible LM Studio path"})

    base_url = fields["OPENAI_BASE_URL"]["value"]
    if base_url and not _is_http_url(base_url):
        issues.append({"field": "OPENAI_BASE_URL", "reason": "must be an http(s) URL"})

    draft_base = fields["DRAFT_MODEL_BASE_URL"]["value"]
    if draft_base and not _is_http_url(draft_base):
        issues.append({"field": "DRAFT_MODEL_BASE_URL", "reason": "must be an http(s) URL"})

    overlay = build_overlay_configuration()
    if overlay["issues"]:
        for issue in overlay["issues"]:
            issues.append({"field": f"overlay.{issue['field']}", "reason": issue["reason"]})

    ok = not issues
    return {
        "ok": ok,
        "status": "ok" if ok else "degraded",
        "profile": profile,
        "dependency_mode": dependency,
        "issues": issues,
        "fields": fields,
        "overlay": overlay,
    }


def validate_runtime_configuration(*, strict: bool = False) -> Dict[str, Any]:
    config = build_runtime_configuration()
    if strict and config["profile"] == "production" and not config["ok"]:
        problems = "; ".join(f"{item['field']}: {item['reason']}" for item in config["issues"])
        raise RuntimeError(f"invalid runtime configuration: {problems}")
    return config


def _outbox_readiness_enforced() -> bool:
    """Whether a stale outbox may fail readiness (ASSISTX_OUTBOX_READINESS).

    Enabled by default; an operator draining a known backlog can set 0 to keep
    /health green without lying about the queue — the payload still reports it.
    """
    raw = os.getenv("ASSISTX_OUTBOX_READINESS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _check_outbox() -> Dict[str, Any]:
    """Queue depth and staleness, read-only so a health probe never writes.

    The outbox is a local SQLite queue, not a network dependency: the probe
    opens the file read-only and only reports. A queue whose oldest deliverable
    event is older than ASSISTX_OUTBOX_STALE_S (default 24 h) is unhealthy —
    depth grows silently otherwise (2026-11: 78 events, 43 days, invisible).
    """
    from .outbox_client import DEFAULT_DB_PATH, read_queue_health

    path = os.getenv("ASSISTX_OUTBOX_DB", DEFAULT_DB_PATH)
    try:
        health = read_queue_health(path)
    except Exception as exc:  # noqa: BLE001 - health must answer
        health = {
            "db_path": path,
            "status": "error",
            "ok": True,
            "reason": f"outbox probe failed: {str(exc)[:200]}",
        }
    health["readiness_enforced"] = _outbox_readiness_enforced()
    return health


def build_runtime_health() -> Dict[str, Any]:
    profile = runtime_profile()
    configuration = build_runtime_configuration()
    dependencies: Dict[str, Dict[str, Any]] = {
        "redis": _check_redis(),
        "neo4j": _check_neo4j(),
        "llm": _check_llm(),
    }
    outbox = _check_outbox()
    overlay_health = {
        "ok": True,
        "status": "skipped",
        "mode": os.getenv("ASSISTX_OVERLAY_MODE", "direct"),
        "reason": "overlay status is reported separately at /api/overlay/status",
    }
    core_ok = dependencies["redis"]["status"] == "ok" and dependencies["neo4j"]["status"] == "ok"
    llm_ok = dependencies["llm"]["status"] in {"ok", "degraded"}
    overlay_ok = overlay_health["status"] in {"ok", "degraded"} and overlay_health["ok"]
    outbox_ok = outbox.get("ok", True) or not outbox.get("readiness_enforced", True)
    overall_ok = core_ok and llm_ok and configuration["ok"] and outbox_ok
    overall_status = "ok" if overall_ok else "degraded"
    return {
        "ok": overall_ok,
        "status": overall_status,
        "service": "auto-assist",
        "version": __version__,
        "uptime": time.time() - _start_time,
        "profile": profile,
        "dependency_mode": dependency_mode(),
        "timestamp": int(time.time() * 1000),
        "configuration": configuration,
        "overlay": overlay_health,
        "dependencies": dependencies,
        "outbox": outbox,
    }


def _check_redis() -> Dict[str, Any]:
    try:
        from .deps import load_redis_module

        redis_module = load_redis_module()
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        client = redis_module.from_url(redis_url, decode_responses=True)
        try:
            ping = client.ping()
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
        # A liveness verdict needs an affirmative confirmation: redis-py's
        # ping() returns bool True on success (or raises).  Treating any other
        # value -- None/0/""/"PONG" from a wrapper or stub -- as healthy let an
        # unconfirmed Redis read as ok in the runtime health snapshot and keep
        # readiness green with zero liveness evidence.
        if ping is not True:
            return {"status": "down", "url": redis_url, "reason": "ping did not confirm liveness"}
        return {"status": "ok", "url": redis_url}
    except Exception as exc:
        return {"status": "down", "url": os.getenv("REDIS_URL", "redis://redis:6379/0"), "reason": str(exc)[:500]}


def _check_neo4j() -> Dict[str, Any]:
    try:
        from .neo4j_client import Neo4jClient

        neo = Neo4jClient()
        try:
            with neo.driver.session() as session:
                session.run("RETURN 1 AS ok").single()
        finally:
            neo.close()
        return {
            "status": "ok",
            "uri": os.getenv("NEO4J_URI"),
            "database": os.getenv("NEO4J_DATABASE") or "default",
        }
    except Exception as exc:
        return {
            "status": "down",
            "uri": os.getenv("NEO4J_URI"),
            "database": os.getenv("NEO4J_DATABASE") or "default",
            "reason": str(exc)[:500],
        }


def _model_ids(resp: Any) -> list[str]:
    """Model ids from an OpenAI-compatible /models response, or [] if unparseable."""
    try:
        payload = resp.json()
    except Exception:
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("id"):
            ids.append(str(entry["id"]))
    return ids


def _llm_fleet_fallback(base_url: str, backend: str, reason: str) -> Dict[str, Any]:
    """The host LM Studio cannot serve, but the operator drives the fleet: models loaded
    on remote LM Studio nodes still count, so report degraded rather than down."""
    try:
        from .llm import client as _lc

        inv = _lc._fleet_model_inventory()
        if inv:
            return {
                "status": "degraded",
                "backend": backend,
                "endpoint": base_url,
                "reason": f"{reason}; {len(inv)} model(s) resident on fleet",
                "fleet_models": len(inv),
            }
    except Exception:
        pass
    return {"status": "degraded", "backend": backend, "endpoint": base_url, "reason": reason}


def _check_llm() -> Dict[str, Any]:
    backend = os.getenv("LLM_BACKEND", "openai").strip().lower()
    timeout = float(os.getenv("LLM_HEALTH_TIMEOUT_S", "3"))
    try:
        if backend == "ollama":
            host = os.getenv("OLLAMA_HOST", "http://ollama:11434").rstrip("/")
            resp = requests.get(f"{host}/api/tags", timeout=timeout)
            if resp.ok:
                return {"status": "ok", "backend": backend, "endpoint": host}
            return {"status": "degraded", "backend": backend, "endpoint": host, "reason": f"HTTP {resp.status_code}"}
        base_url = os.getenv("OPENAI_BASE_URL", "http://host.docker.internal:1234/v1").rstrip("/")
        resp = requests.get(f"{base_url}/models", timeout=timeout)
        if resp.ok:
            # A reachable /models is not a usable LLM. An OpenAI-compatible server
            # answers 200 with an empty data list when nothing is loaded, which is the
            # state this host sits in whenever the soak holds the card. Reporting ok
            # there let a genuine LLM outage read as healthy.
            models = _model_ids(resp)
            if models:
                return {
                    "status": "ok",
                    "backend": backend,
                    "endpoint": base_url,
                    "models": len(models),
                }
            return _llm_fleet_fallback(
                base_url,
                backend,
                "no models resident on local LM Studio",
            )
        # The host/local LM Studio is the default probe target, but the
        # operator drives the fleet: models are loaded on remote LM Studio
        # nodes, not necessarily on the host.  If the local probe is down but
        # the fleet actually has models resident in VRAM, the LLM dependency
        # is satisfied — report degraded (not down) so dependent services
        # (e.g. auto-assign's neo4j brain) still treat assistx as reachable.
        try:
            from .llm import client as _lc
            inv = _lc._fleet_model_inventory()
            if inv:
                return {
                    "status": "degraded",
                    "backend": backend,
                    "endpoint": base_url,
                    "reason": f"local LM Studio unreachable; {len(inv)} model(s) resident on fleet",
                    "fleet_models": len(inv),
                }
        except Exception:
            pass
        return {"status": "degraded", "backend": backend, "endpoint": base_url, "reason": f"HTTP {resp.status_code}"}
    except Exception as exc:
        # Same fleet fallback for connection failures (host LM Studio down,
        # operator runs models on the fleet instead).
        try:
            from .llm import client as _lc
            inv = _lc._fleet_model_inventory()
            if inv:
                return {
                    "status": "degraded",
                    "backend": backend,
                    "endpoint": base_url,
                    "reason": f"local LM Studio unreachable; {len(inv)} model(s) resident on fleet",
                    "fleet_models": len(inv),
                }
        except Exception:
            pass
        return {"status": "down", "backend": backend, "reason": str(exc)[:500]}
