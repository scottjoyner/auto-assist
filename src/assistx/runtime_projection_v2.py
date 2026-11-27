from __future__ import annotations

import base64
import copy
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
from .fleet_routing_projection import (
    benchmark_projection_index,
    node_routing_policy_index,
)
from .tool_call_reliability import model_identity_fingerprint

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
    """Attach advisory benchmark evidence to already admitted models only."""

    try:
        benchmark_index = benchmark_projection_index(neo_factory)
        node_policy = node_routing_policy_index(neo_factory)
    except Exception:
        return
    for provider in document.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        node_id = str(provider.get("node_id") or "")
        provider_policy = node_policy.get(node_id)
        if provider_policy is not None:
            provider["routing_roles"] = provider_policy["routing_roles"]
            provider["worker_mode"] = provider_policy["worker_mode"]
            provider["allow_agent_runtime"] = provider_policy[
                "allow_agent_runtime"
            ]
            provider["allow_code_execution"] = provider_policy[
                "allow_code_execution"
            ]
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            aliases = (
                str(model.get("alias") or ""),
                str(model.get("provider_model") or ""),
            )
            hint = next(
                (
                    benchmark_index[(node_id, alias)]
                    for alias in aliases
                    if (node_id, alias) in benchmark_index
                ),
                None,
            )
            policy = hint or provider_policy
            if policy is None:
                continue
            model["routing_roles"] = policy["routing_roles"]
            model["worker_mode"] = policy["worker_mode"]
            model["allow_agent_runtime"] = policy[
                "allow_agent_runtime"
            ]
            model["allow_code_execution"] = policy[
                "allow_code_execution"
            ]
            scores = (
                copy.deepcopy(hint["task_family_scores"])
                if hint is not None
                else {}
            )
            current_identity_fingerprint = model_identity_fingerprint(
                {
                    "node_id": node_id,
                    "runtime_instance_id": provider.get(
                        "runtime_instance_id"
                    ),
                    "runtime_version": provider.get("runtime_version"),
                    "model_instance_id": model.get("model_instance_id"),
                    "artifact_fingerprint": model.get(
                        "artifact_fingerprint"
                    ),
                    "quantization": model.get("quantization"),
                    "context_length": model.get("context_window"),
                }
            )

            existing_tool_score = scores.get("tool_use")
            if (
                isinstance(existing_tool_score, dict)
                and existing_tool_score.get("tool_call_probe")
            ):
                if (
                    not current_identity_fingerprint
                    or existing_tool_score.get(
                        "model_identity_fingerprint"
                    )
                    != current_identity_fingerprint
                ):
                    existing_tool_score.pop("tool_call_probe", None)
                    existing_tool_score["quality_floor_passed"] = False

            probe = (
                hint.get("tool_call_probe_evidence")
                if hint is not None
                else None
            )
            if isinstance(probe, dict):
                try:
                    not_before = int(
                        probe.get("not_before_generation") or 0
                    )
                    evidence_expiry = int(
                        probe.get("expires_at_ts") or 0
                    )
                except (TypeError, ValueError):
                    not_before = 0
                    evidence_expiry = 0
                eligible = (
                    current_identity_fingerprint
                    and probe.get("model_identity_fingerprint")
                    == current_identity_fingerprint
                    and int(document.get("generation") or 0)
                    >= not_before
                    and not_before > 0
                    and evidence_expiry
                    > int(document.get("generated_at_ms") or 0)
                    and isinstance(probe.get("tool_call_probe"), dict)
                )
                if eligible:
                    tool_score = scores.setdefault("tool_use", {})
                    tool_score.update(
                        {
                            "quality_score": probe.get(
                                "quality_score"
                            ),
                            "quality_floor_passed": bool(
                                probe.get("quality_floor_passed")
                            ),
                            "tool_call_probe": probe.get(
                                "tool_call_probe"
                            ),
                            "model_identity_fingerprint": (
                                current_identity_fingerprint
                            ),
                        }
                    )
                    document["expires_at_ms"] = min(
                        int(document["expires_at_ms"]),
                        evidence_expiry,
                    )
            model["task_family_scores"] = scores


def build_runtime_projection(
    neo_factory: Callable[[], Any],
    *,
    ttl_seconds: int = 60,
    now_ms: int | None = None,
    private_key: Ed25519PrivateKey | None = None,
    key_id: str | None = None,
) -> dict[str, Any]:
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
    signer = private_key or load_private_key()
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
