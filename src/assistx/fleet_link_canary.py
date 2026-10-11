from __future__ import annotations

import hmac
import os
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .mobile_agent_routes import _mobile_model_handle


_MODEL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,64}/[a-zA-Z0-9][a-zA-Z0-9._-]{0,64}$")
_TRACE_RE = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_MAX_READ = 128_000


class FleetLinkCanaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_handle: str = Field(pattern=r"^model:v1:[0-9a-f]{32}$")
    prompt: str = Field(min_length=1, max_length=256)
    max_tokens: int = Field(default=64, ge=1, le=64)


def _canary_url() -> str:
    if os.getenv("ASSISTX_FLEET_LINK_CANARY", "").strip() != "1":
        raise ValueError("operator canary disabled")
    raw = os.getenv("ASSISTX_FLEET_LINK_URL", "").strip()
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or not 1024 <= parsed.port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.netloc != f"127.0.0.1:{parsed.port}"
    ):
        raise ValueError("operator must pin a literal 127.0.0.1 sidecar endpoint")
    if not os.getenv("ASSISTX_FLEET_LINK_RUNTIME_ID", "").strip():
        raise ValueError("operator must pin an approved runtime ID")
    return f"http://127.0.0.1:{parsed.port}"


def _trace(traceparent: str | None) -> tuple[str | None, str | None]:
    if traceparent is None:
        return None, None
    match = _TRACE_RE.fullmatch(traceparent)
    if not match or not int(match[1], 16) or not int(match[2], 16):
        raise ValueError("untrusted trace context")
    return traceparent, match[1]


def _approved_candidates(
    projection: dict[str, Any],
    url: str,
) -> dict[str, tuple[str, str]]:
    """Only use exact signed-projection runtime, route, and fingerprint mapping.

    No alias/fuzzy matching, model-name-only fallback, or provider autoload.
    """
    try:
        expiry = int(projection.get("expires_at_ms") or 0)
    except (ValueError, TypeError):
        return {}
    if expiry <= int(time.time() * 1000):
        return {}
    runtime_id = os.getenv("ASSISTX_FLEET_LINK_RUNTIME_ID", "").strip()
    candidates: dict[str, tuple[str, str]] = {}
    poisoned: set[str] = set()
    for provider in projection.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        if (
            provider.get("enabled") is not True
            or provider.get("runtime_instance_id") != runtime_id
            or provider.get("runtime_kind") != "openai_compatible"
            or provider.get("type") != "openai_compatible"
            or provider.get("base_url") != url + "/v1"
            or provider.get("access_urls") != [url + "/v1"]
            or not str(provider.get("node_id") or "").strip()
        ):
            continue
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            route = model.get("provider_model")
            fingerprint = model.get("artifact_fingerprint")
            handle = _mobile_model_handle(model)
            caps = model.get("capabilities") or []
            if (
                not isinstance(route, str)
                or not _MODEL_RE.fullmatch(route)
                or route.startswith("auto/")
                or not isinstance(fingerprint, str)
                or not fingerprint.strip()
                or not handle
                or not isinstance(caps, list)
                or "chat" not in caps
                or "local_only" not in caps
                or bool(model.get("allow_code_execution"))
            ):
                continue
            item = (route, fingerprint)
            if handle in candidates and candidates[handle] != item:
                poisoned.add(handle)
            candidates[handle] = item
    return {h: value for h, value in candidates.items() if h not in poisoned}


def _ready_instances(document: Any, inventory: Any) -> set[str]:
    if not isinstance(document, dict) or document.get("schema") != "fleet-model-link.v1":
        return set()
    if not isinstance(inventory, dict) or inventory.get("object") != "list":
        return set()
    rows = document.get("models")
    data = inventory.get("data")
    if not isinstance(rows, list) or not isinstance(data, list) or len(rows) > 64 or len(data) > 64:
        return set()
    listed = {
        item.get("id") for item in data if isinstance(item, dict)
        and isinstance(item.get("id"), str)
    }
    return {
        item["id"] for item in rows
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and _MODEL_RE.fullmatch(item["id"])
        and item.get("kind") == "chat"
        and item.get("ready_now") is True
        and item.get("status") == "READY_PROVED_RECENTLY"
        and item["id"] in listed
    }


async def _json_get(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    response = await client.get(url, headers={"Accept": "application/json"})
    if response.status_code != 200 or len(response.content) > _MAX_READ:
        raise ValueError("sidecar inventory unavailable")
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("invalid sidecar inventory")
    return result


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(4.0, connect=1.0),
        follow_redirects=False,
        trust_env=False,
    )


def build_fleet_link_canary_router(
    auth_dependency: Callable[..., Any],
    projection_factory: Callable[[], dict[str, Any]],
    client_factory: Callable[[], httpx.AsyncClient] = _make_client,
) -> APIRouter:
    """Fully opt-in operator-only AssistX shadow admission, not mobile promotion."""
    def require_operator(
        identity: str = Depends(auth_dependency),
        x_assistx_fleet_operator_token: str | None = Header(default=None),
    ) -> str:
        # A trusted identity header can be spoofed if an ingress is misconfigured.
        # Require independent operator proof even for allowlisted identities.
        allowed = {name.strip() for name in os.getenv(
            "ASSISTX_FLEET_LINK_OPERATOR_USERS", ""
        ).split(",") if name.strip()}
        expected = os.getenv("ASSISTX_FLEET_LINK_OPERATOR_TOKEN", "")
        if (
            not isinstance(identity, str)
            or not identity
            or identity not in allowed
            or len(expected) < 32
            or not x_assistx_fleet_operator_token
            or not hmac.compare_digest(
                expected.encode("utf-8"),
                x_assistx_fleet_operator_token.encode("utf-8"),
            )
        ):
            raise HTTPException(status_code=403, detail="fleet link operator forbidden")
        return identity

    router = APIRouter(
        prefix="/api/fleet-link",
        tags=["fleet-link-canary"],
        dependencies=[Depends(require_operator)],
    )

    @router.get("/preview")
    async def preview() -> dict[str, Any]:
        try:
            url = _canary_url()
            projection = projection_factory()
            approved = _approved_candidates(projection, url)
            async with client_factory() as client:
                status = await _json_get(client, url + "/fleet/nodes")
                models = await _json_get(client, url + "/v1/models")
            ready = _ready_instances(status, models)
            return {
                "schema": "assistx-fleet-link-canary.v1",
                "mode": "OPERATOR_OBSERVATION_ONLY",
                "admitted_model_count": len(approved),
                "ready_model_count": len({h for h, (route, _) in approved.items() if route in ready}),
                "execution_authorized": False,
                "automatic_promotion": False,
                "catalog_generation_hint": status.get("generation") if isinstance(status.get("generation"), int) else None,
            }
        except (ValueError, TypeError, httpx.HTTPError, OSError):
            return {
                "schema": "assistx-fleet-link-canary.v1",
                "mode": "NOT_VERIFIED",
                "admitted_model_count": 0,
                "ready_model_count": 0,
                "execution_authorized": False,
                "automatic_promotion": False,
                "catalog_generation_hint": None,
            }
        except Exception:
            # Projection signer/Neo4j availability must never spill internals.
            return {
                "schema": "assistx-fleet-link-canary.v1",
                "mode": "NOT_VERIFIED",
                "admitted_model_count": 0,
                "ready_model_count": 0,
                "execution_authorized": False,
                "automatic_promotion": False,
                "catalog_generation_hint": None,
            }

    @router.post("/canary")
    async def canary(
        body: FleetLinkCanaryRequest,
        traceparent: str | None = Header(default=None),
    ):
        try:
            url = _canary_url()
            trace, trace_id = _trace(traceparent)
            projection = projection_factory()
            candidate = _approved_candidates(projection, url).get(body.model_handle)
            if not candidate:
                raise ValueError("model not independently admitted")
            route, _fingerprint = candidate
            async with client_factory() as client:
                status = await _json_get(client, url + "/fleet/nodes")
                models = await _json_get(client, url + "/v1/models")
                if route not in _ready_instances(status, models):
                    raise ValueError("model not live/proven")
                headers = {"Content-Type": "application/json", "Accept": "application/json"}
                if trace is not None:
                    headers["traceparent"] = trace
                response = await client.post(
                    url + "/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": route,
                        "stream": False,
                        "max_tokens": body.max_tokens,
                        "messages": [{"role": "user", "content": body.prompt}],
                    },
                )
                if response.status_code != 200 or len(response.content) > _MAX_READ:
                    raise ValueError("canary inference unavailable")
                completion = response.json()
                if (
                    not isinstance(completion, dict)
                    or completion.get("model") != route
                    or not isinstance(completion.get("choices"), list)
                    or len(completion["choices"]) != 1
                    or not isinstance(completion["choices"][0], dict)
                ):
                    raise ValueError("canary completion invalid")
                if completion["choices"][0].get("finish_reason") != "stop":
                    raise ValueError("canary completion did not stop normally")
                message = completion["choices"][0].get("message")
                if (
                    not isinstance(message, dict)
                    or message.get("role") != "assistant"
                    or not isinstance(message.get("content"), str)
                    or len(message["content"]) > 4096
                ):
                    raise ValueError("unexpected canary payload")
                reported_trace = response.headers.get("X-Fleet-Trace-Id", "")
                if trace_id and reported_trace != trace_id:
                    raise ValueError("trace witness mismatch")
                if reported_trace and not re.fullmatch(r"[0-9a-f]{32}", reported_trace):
                    raise ValueError("invalid trace witness")
                return JSONResponse(
                    content={
                        "object": "chat.completion",
                        "model": body.model_handle,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": message["content"],
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "canary_only": True,
                        "no_automatic_dispatch": True,
                    },
                    headers={
                        "Cache-Control": "no-store",
                        "X-Fleet-Trace-Id": reported_trace,
                    } if reported_trace else {"Cache-Control": "no-store"},
                )
        except (ValueError, TypeError, KeyError, httpx.HTTPError, OSError):
            raise HTTPException(status_code=503, detail={"error": "fleet_link_canary_unavailable"})
        except Exception:
            raise HTTPException(status_code=503, detail={"error": "fleet_link_canary_unavailable"})

    return router
