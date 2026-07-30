from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException


class RuntimeProjectionBlocked(RuntimeError):
    pass


def _query_rows(
    neo_factory: Callable[[], Any],
    query: str,
    parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    neo = neo_factory()
    try:
        with neo._session() as session:
            return [dict(row) for row in session.run(query, parameters or {})]
    finally:
        neo.close()


def _json_value(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _known(value: Any) -> bool:
    return str(value or "").strip().lower() not in {
        "",
        "unknown",
        "unresolved",
        "none",
        "null",
    }


def _provider_type(runtime_kind: Any) -> str | None:
    value = str(runtime_kind or "").strip().lower().replace("-", "_")
    if "lmstudio" in value or "lm_studio" in value:
        return "lmstudio"
    if "llama" in value:
        return "llama_cpp"
    if "vllm" in value:
        return "vllm"
    if "sglang" in value:
        return "sglang"
    if value in {"openai_compatible", "openai-compatible"}:
        return "openai_compatible"
    return None


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip())
    return text.strip("-") or "runtime"


def _canonical_unsigned(document: dict[str, Any]) -> bytes:
    payload = {
        key: value
        for key, value in document.items()
        if key not in {"checksum", "signature"}
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def projection_checksum(document: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_unsigned(document)).hexdigest()


def projection_signature(generation: int, checksum: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"{generation}:{checksum}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _projection_state(neo_factory: Callable[[], Any]) -> dict[str, Any]:
    rows = _query_rows(
        neo_factory,
        """
        MATCH (s:FleetProjectionState {name:'canonical'})
        RETURN s.generation AS generation,
               s.revision AS revision,
               s.status AS status,
               s.updated_at_ts AS updated_at_ts,
               s.approved_by AS approved_by,
               s.approval_id AS approval_id
        LIMIT 1
        """,
    )
    if not rows:
        raise RuntimeProjectionBlocked(
            "FleetProjectionState{name:'canonical'} is missing"
        )
    state = rows[0]
    if str(state.get("status") or "").lower() != "approved":
        raise RuntimeProjectionBlocked("canonical fleet projection is not approved")
    try:
        generation = int(state.get("generation") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeProjectionBlocked("projection generation is invalid") from exc
    if generation <= 0 or not _known(state.get("revision")):
        raise RuntimeProjectionBlocked(
            "projection generation and revision must be resolved"
        )
    if not _known(state.get("approved_by")) or not _known(
        state.get("approval_id")
    ):
        raise RuntimeProjectionBlocked(
            "projection requires approved_by and approval_id evidence"
        )
    return {**state, "generation": generation}


def _runtime_rows(neo_factory: Callable[[], Any], now_ms: int) -> list[dict[str, Any]]:
    return _query_rows(
        neo_factory,
        """
        MATCH (r:RuntimeInstance)
        WHERE coalesce(r.admitted, false) = true
          AND toLower(coalesce(r.status, 'unknown')) IN ['online','healthy','ready']
          AND coalesce(r.expires_at_ts, $now_ms + 1) > $now_ms
        OPTIONAL MATCH (r)-[:SERVES]->(m:LoadedModelInstance)
        OPTIONAL MATCH (m)-[:INSTANCE_OF]->(a:ModelArtifact)
        RETURN r.runtime_instance_id AS runtime_instance_id,
               r.node_id AS node_id,
               r.runtime_kind AS runtime_kind,
               r.runtime_version AS runtime_version,
               r.headless AS headless,
               r.process_id AS process_id,
               r.updated_at_ts AS updated_at_ts,
               collect(DISTINCT {
                   admitted: m.admitted,
                   expires_at_ts: m.expires_at_ts,
                   model_instance_id: m.model_instance_id,
                   model_key: m.model_key,
                   provider_model: coalesce(m.provider_model, m.model_id, m.model_key),
                   artifact_fingerprint: coalesce(m.artifact_fingerprint, a.artifact_fingerprint, a.sha256),
                   quantization: coalesce(m.quantization, a.quantization),
                   context_length: coalesce(m.context_length, a.context_length),
                   capabilities_json: coalesce(m.capabilities_json, a.capabilities_json),
                   updated_at_ts: m.updated_at_ts
               }) AS loaded_models
        ORDER BY r.node_id, r.runtime_instance_id
        """,
        {"now_ms": now_ms},
    )


def _access_rows(neo_factory: Callable[[], Any], now_ms: int) -> list[dict[str, Any]]:
    return _query_rows(
        neo_factory,
        """
        MATCH (a:AccessPath)
        WHERE coalesce(a.approved, false) = true
          AND coalesce(a.expires_at_ts, 0) > $now_ms
        RETURN a.runtime_instance_id AS runtime_instance_id,
               coalesce(a.base_url, a.url) AS base_url,
               a.transport AS transport,
               coalesce(a.preference, a.priority, 100) AS preference,
               a.observed_at_ts AS observed_at_ts,
               a.expires_at_ts AS expires_at_ts,
               a.approved_by AS approved_by,
               a.approval_id AS approval_id
        ORDER BY a.runtime_instance_id,
                 coalesce(a.preference, a.priority, 100),
                 a.observed_at_ts DESC
        """,
        {"now_ms": now_ms},
    )


def _capacity_rows(neo_factory: Callable[[], Any], now_ms: int) -> list[dict[str, Any]]:
    return _query_rows(
        neo_factory,
        """
        MATCH (c:CapacityObservation)
        WHERE coalesce(c.approved, false) = true
          AND coalesce(c.expires_at_ts, 0) > $now_ms
        RETURN c.runtime_instance_id AS runtime_instance_id,
               c.parallel_slots AS parallel_slots,
               c.queue_limit AS queue_limit,
               c.queue_timeout_seconds AS queue_timeout_seconds,
               c.observed_at_ts AS observed_at_ts,
               c.expires_at_ts AS expires_at_ts,
               c.approved_by AS approved_by,
               c.approval_id AS approval_id
        ORDER BY c.runtime_instance_id, c.observed_at_ts DESC
        """,
        {"now_ms": now_ms},
    )


def build_runtime_projection(
    neo_factory: Callable[[], Any],
    *,
    secret: str,
    ttl_seconds: int = 60,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if not secret.strip():
        raise RuntimeProjectionBlocked(
            "ASSISTX_RUNTIME_PROJECTION_HMAC_SECRET is required"
        )
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    state = _projection_state(neo_factory)
    runtimes = _runtime_rows(neo_factory, now)
    access_rows = _access_rows(neo_factory, now)
    capacity_rows = _capacity_rows(neo_factory, now)

    access_by_runtime: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in access_rows:
        runtime_id = str(row.get("runtime_instance_id") or "").strip()
        url = str(row.get("base_url") or "").strip().rstrip("/")
        if not runtime_id or not url:
            continue
        if not _known(row.get("approved_by")) or not _known(
            row.get("approval_id")
        ):
            continue
        if url not in [item["base_url"] for item in access_by_runtime[runtime_id]]:
            access_by_runtime[runtime_id].append({**row, "base_url": url})

    capacity_by_runtime: dict[str, dict[str, Any]] = {}
    for row in capacity_rows:
        runtime_id = str(row.get("runtime_instance_id") or "").strip()
        if not runtime_id or runtime_id in capacity_by_runtime:
            continue
        if not _known(row.get("approved_by")) or not _known(
            row.get("approval_id")
        ):
            continue
        try:
            slots = int(row.get("parallel_slots") or 0)
            queue_limit = int(row.get("queue_limit") or 0)
            queue_timeout = float(row.get("queue_timeout_seconds") or 0)
        except (TypeError, ValueError):
            continue
        if slots <= 0 or queue_limit < 0 or queue_timeout < 0:
            continue
        capacity_by_runtime[runtime_id] = {
            **row,
            "parallel_slots": slots,
            "queue_limit": queue_limit,
            "queue_timeout_seconds": queue_timeout,
        }

    providers: list[dict[str, Any]] = []
    for runtime in runtimes:
        runtime_id = str(runtime.get("runtime_instance_id") or "").strip()
        node_id = str(runtime.get("node_id") or "").strip()
        runtime_kind = str(runtime.get("runtime_kind") or "").strip()
        runtime_version = str(runtime.get("runtime_version") or "").strip()
        provider_type = _provider_type(runtime_kind)
        paths = access_by_runtime.get(runtime_id) or []
        capacity = capacity_by_runtime.get(runtime_id)
        if not all(
            (
                _known(runtime_id),
                _known(node_id),
                _known(runtime_kind),
                _known(runtime_version),
                provider_type,
                paths,
                capacity,
            )
        ):
            continue

        models = []
        for model in runtime.get("loaded_models") or []:
            if not isinstance(model, dict):
                continue
            if model.get("admitted") is not True:
                continue
            expires_at = model.get("expires_at_ts")
            if expires_at is not None:
                try:
                    if int(expires_at) <= now:
                        continue
                except (TypeError, ValueError):
                    continue
            required = (
                model.get("model_instance_id"),
                model.get("model_key"),
                model.get("provider_model"),
                model.get("artifact_fingerprint"),
                model.get("quantization"),
                model.get("context_length"),
            )
            if not all(_known(value) for value in required):
                continue
            try:
                context_length = int(model.get("context_length") or 0)
            except (TypeError, ValueError):
                continue
            if context_length <= 0:
                continue
            capabilities = _json_value(
                model.get("capabilities_json"),
                ["chat", "streaming", "local_only"],
            )
            if not isinstance(capabilities, list):
                capabilities = ["chat", "streaming", "local_only"]
            models.append(
                {
                    "alias": str(model.get("model_key")),
                    "provider_model": str(model.get("provider_model")),
                    "model_instance_id": str(model.get("model_instance_id")),
                    "artifact_fingerprint": str(
                        model.get("artifact_fingerprint")
                    ),
                    "quantization": str(model.get("quantization")),
                    "capabilities": sorted(
                        {str(item) for item in capabilities if str(item).strip()}
                        | {"local_only"}
                    ),
                    "context_window": context_length,
                }
            )
        if not models:
            continue

        providers.append(
            {
                "name": f"assistx-{_slug(node_id)}-{_slug(runtime_id)}",
                "type": provider_type,
                "node_id": node_id,
                "runtime_instance_id": runtime_id,
                "runtime_kind": runtime_kind,
                "runtime_version": runtime_version,
                "headless": runtime.get("headless"),
                "parallel_slots": capacity["parallel_slots"],
                "queue_limit": capacity["queue_limit"],
                "queue_timeout_seconds": capacity[
                    "queue_timeout_seconds"
                ],
                "enabled": True,
                "base_url": paths[0]["base_url"],
                "access_urls": [item["base_url"] for item in paths],
                "priority": 100,
                "quota_class": "local",
                "models": models,
            }
        )

    if not providers:
        raise RuntimeProjectionBlocked(
            "no fully approved, fresh, identity-complete runtime is projectable"
        )

    ttl = max(5, min(int(ttl_seconds), 900))
    document: dict[str, Any] = {
        "schema_version": "1",
        "source": "assistx",
        "generation": state["generation"],
        "revision": str(state["revision"]),
        "generated_at_ms": now,
        "expires_at_ms": now + ttl * 1000,
        "providers": providers,
    }
    document["checksum"] = projection_checksum(document)
    document["signature"] = projection_signature(
        document["generation"],
        document["checksum"],
        secret,
    )
    return document


def build_runtime_projection_router(
    neo_factory: Callable[[], Any],
    auth_dependency: Any | None = None,
) -> APIRouter:
    dependencies = (
        [Depends(auth_dependency)] if auth_dependency is not None else []
    )
    router = APIRouter(
        prefix="/api/router",
        tags=["auto-router"],
        dependencies=dependencies,
    )

    @router.get("/runtime-projection")
    def runtime_projection() -> dict[str, Any]:
        try:
            return build_runtime_projection(
                neo_factory,
                secret=os.getenv(
                    "ASSISTX_RUNTIME_PROJECTION_HMAC_SECRET",
                    "",
                ),
                ttl_seconds=int(
                    os.getenv("ASSISTX_RUNTIME_PROJECTION_TTL_SECONDS", "60")
                ),
            )
        except RuntimeProjectionBlocked as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
