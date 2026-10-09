from __future__ import annotations

import base64
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, HTTPException

from . import runtime_projection as legacy
from .fleet_routing_projection import benchmark_projection_index, node_routing_policy_index


_ALGORITHM = "Ed25519"
_DEFAULT_KEY_ID = "assistx-runtime-projection-v1"
_INTERNAL_COMPAT_SECRET = "assistx-ed25519-projection-wrapper"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _private_key_bytes_from_env() -> bytes:
    path_value = os.getenv("ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_FILE", "").strip()
    if path_value:
        path = Path(path_value)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise legacy.RuntimeProjectionBlocked(
                "runtime projection private key permissions must be 0600 or stricter"
            )
        return path.read_bytes()
    value = os.getenv("ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_PEM", "")
    return value.replace("\\n", "\n").encode("utf-8") if value else b""


def load_private_key() -> Ed25519PrivateKey:
    raw = _private_key_bytes_from_env()
    if not raw:
        raise legacy.RuntimeProjectionBlocked(
            "ASSISTX_RUNTIME_PROJECTION_SIGNING_KEY_FILE is required"
        )
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except ValueError:
        try:
            decoded = base64.urlsafe_b64decode(raw.decode("ascii") + "==")
            key = Ed25519PrivateKey.from_private_bytes(decoded)
        except Exception as exc:
            raise legacy.RuntimeProjectionBlocked(
                "runtime projection private key is invalid"
            ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise legacy.RuntimeProjectionBlocked(
            "runtime projection private key must be Ed25519"
        )
    return key


def signing_message(document: dict[str, Any]) -> bytes:
    payload = {
        "schema_version": str(document.get("schema_version") or ""),
        "source": str(document.get("source") or ""),
        "generation": int(document.get("generation") or 0),
        "revision": str(document.get("revision") or ""),
        "checksum": str(document.get("checksum") or ""),
        "generated_at_ms": int(document.get("generated_at_ms") or 0),
        "expires_at_ms": int(document.get("expires_at_ms") or 0),
        "signature_algorithm": str(document.get("signature_algorithm") or ""),
        "signature_key_id": str(document.get("signature_key_id") or ""),
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def projection_signature(
    document: dict[str, Any],
    private_key: Ed25519PrivateKey,
) -> str:
    return _b64url(private_key.sign(signing_message(document)))



def _apply_benchmark_routing(
    document: dict[str, Any],
    neo_factory: Callable[[], Any],
) -> None:
    """Annotate existing admitted providers/models; never create admissions.

    The primary signed projection already fences runtime identity, evidence
    freshness, paths and loaded models. Benchmark metadata is advisory and
    must not introduce any additional provider or model into that projection.
    """
    try:
        node_policies = node_routing_policy_index(neo_factory)
        model_evidence = benchmark_projection_index(neo_factory)
    except Exception as exc:
        raise legacy.RuntimeProjectionBlocked(
            "routing policy and benchmark evidence unavailable"
        ) from exc

    fields = ("routing_roles", "worker_mode", "allow_agent_runtime", "allow_code_execution")
    deny_policy = {
        "routing_roles": [],
        "worker_mode": "observer_only",
        "allow_agent_runtime": False,
        "allow_code_execution": False,
    }
    for provider in document.get("providers", []):
        if not isinstance(provider, dict):
            continue
        node_id = str(provider.get("node_id") or "").strip()
        source_policy = node_policies.get(node_id) or {}
        policy = {key: source_policy.get(key, deny_policy[key]) for key in fields}
        # Boolean authority is granted only by literal graph boolean True.
        policy["allow_agent_runtime"] = policy["allow_agent_runtime"] is True
        policy["allow_code_execution"] = policy["allow_code_execution"] is True
        policy["routing_roles"] = (
            sorted({str(role) for role in policy["routing_roles"] if isinstance(role, str)})
            if isinstance(policy["routing_roles"], list)
            else []
        )
        policy["worker_mode"] = str(policy["worker_mode"] or "observer_only")
        provider.update(policy)
        for model in provider.get("models", []):
            if not isinstance(model, dict):
                continue
            # Match only an admitted loaded-model alias; never use arbitrary
            # submitted provider_model values to match another model's scores.
            model_key = str(model.get("alias") or "").strip()
            evidence = model_evidence.get((node_id, model_key), {})
            model.update(policy)
            scores = evidence.get("task_family_scores", {}) if isinstance(evidence, dict) else {}
            model["task_family_scores"] = scores if isinstance(scores, dict) else {}



def build_runtime_projection(
    neo_factory: Callable[[], Any],
    *,
    ttl_seconds: int = 60,
    now_ms: int | None = None,
    private_key: Ed25519PrivateKey | None = None,
    key_id: str | None = None,
) -> dict[str, Any]:
    signer = private_key or load_private_key()
    document = legacy.build_runtime_projection(
        neo_factory,
        secret=_INTERNAL_COMPAT_SECRET,
        ttl_seconds=ttl_seconds,
        now_ms=now_ms,
    )
    _apply_benchmark_routing(document, neo_factory)
    document["schema_version"] = "2"
    document["signature_algorithm"] = _ALGORITHM
    document["signature_key_id"] = (
        key_id
        or os.getenv("ASSISTX_RUNTIME_PROJECTION_KEY_ID", _DEFAULT_KEY_ID).strip()
        or _DEFAULT_KEY_ID
    )
    document.pop("signature", None)
    document["checksum"] = legacy.projection_checksum(document)
    document["signature"] = projection_signature(document, signer)
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
                ttl_seconds=int(
                    os.getenv("ASSISTX_RUNTIME_PROJECTION_TTL_SECONDS", "60")
                ),
            )
        except legacy.RuntimeProjectionBlocked as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return router
