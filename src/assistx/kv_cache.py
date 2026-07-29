from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

CACHE_SCHEMA_VERSION = 1
ACTIVE_CACHE_STATUSES = {"READY", "RESTORING"}
STORAGE_TIERS = {"gpu", "host", "local_disk", "distributed"}
PRIVACY_SCOPES = {"private", "project", "fleet"}
RUNTIME_CAPABILITIES = {
    "lmstudio": {
        "prefix_affinity": True,
        "export_restore": False,
        "distributed_restore": False,
        "configuration": ["kv_quantization", "offload_kv_cache_to_gpu"],
    },
    "llama_cpp": {
        "prefix_affinity": True,
        "export_restore": True,
        "distributed_restore": False,
        "adapter": "slot_save_restore",
    },
    "vllm": {
        "prefix_affinity": True,
        "export_restore": False,
        "distributed_restore": False,
        "adapter": "automatic_prefix_caching",
    },
    "sglang": {
        "prefix_affinity": True,
        "export_restore": True,
        "distributed_restore": True,
        "adapter": "hicache",
    },
    "openai_compatible": {
        "prefix_affinity": False,
        "export_restore": False,
        "distributed_restore": False,
    },
}

_COMPATIBILITY_FIELDS = (
    "model_artifact_hash",
    "model_id",
    "model_quantization",
    "kv_k_quantization",
    "kv_v_quantization",
    "tokenizer_hash",
    "chat_template_hash",
    "adapter_hash",
    "runtime",
    "runtime_version",
    "cache_format_version",
    "context_length",
    "rope_config_hash",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _required_text(value: Any, name: str, max_length: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return text


def prefix_digest(
    token_ids: Iterable[int],
    *,
    secret: str,
    privacy_scope: str,
    scope_id: str,
) -> str:
    """Return an opaque, scope-bound prefix ID without retaining prompt text."""
    if not secret:
        raise ValueError("a prefix digest secret is required")
    scope = str(privacy_scope or "").lower()
    if scope not in PRIVACY_SCOPES:
        raise ValueError(f"unsupported privacy scope: {privacy_scope}")
    normalized_tokens = [int(token) for token in token_ids]
    if not normalized_tokens:
        raise ValueError("at least one token ID is required")
    if any(token < 0 for token in normalized_tokens):
        raise ValueError("token IDs must be non-negative")
    identity = {
        "v": CACHE_SCHEMA_VERSION,
        "privacy_scope": scope,
        "scope_id": _required_text(scope_id, "scope_id"),
        "token_ids": normalized_tokens,
    }
    return "prefix-" + hmac.new(
        secret.encode(),
        _canonical(identity),
        hashlib.sha256,
    ).hexdigest()


def compatibility_fingerprint(spec: Mapping[str, Any]) -> str:
    """Fingerprint every setting that can change KV tensor interpretation."""
    normalized = {
        field: spec.get(field)
        for field in _COMPATIBILITY_FIELDS
    }
    for field in (
        "model_artifact_hash",
        "model_id",
        "model_quantization",
        "tokenizer_hash",
        "chat_template_hash",
        "runtime",
        "runtime_version",
        "cache_format_version",
        "context_length",
        "rope_config_hash",
    ):
        if normalized.get(field) in (None, ""):
            raise ValueError(f"{field} is required for KV-cache compatibility")
    normalized["context_length"] = int(normalized["context_length"])
    if normalized["context_length"] <= 0:
        raise ValueError("context_length must be positive")
    return hashlib.sha256(_canonical(normalized)).hexdigest()


def runtime_capabilities(
    runtime: str,
    advertised: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    base = dict(
        RUNTIME_CAPABILITIES.get(
            str(runtime or "").lower(),
            RUNTIME_CAPABILITIES["openai_compatible"],
        )
    )
    allowed = {
        "prefix_affinity",
        "export_restore",
        "distributed_restore",
    }
    for key, value in (advertised or {}).items():
        if key in allowed:
            # A node may narrow a known runtime capability, never promote an
            # unknown or unsupported backend into a mutation-capable adapter.
            base[key] = bool(base.get(key)) and bool(value)
    return base


def build_manifest(data: Mapping[str, Any], *, now_ms: int | None = None) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    prefix_id = _required_text(data.get("prefix_id"), "prefix_id")
    node_id = _required_text(data.get("node_id"), "node_id")
    endpoint_id = _required_text(data.get("endpoint_id"), "endpoint_id")
    model_id = _required_text(data.get("model_id"), "model_id")
    runtime = _required_text(data.get("runtime"), "runtime").lower()
    privacy_scope = str(data.get("privacy_scope") or "private").lower()
    if privacy_scope not in PRIVACY_SCOPES:
        raise ValueError(f"unsupported privacy scope: {privacy_scope}")
    storage_tier = str(data.get("storage_tier") or "gpu").lower()
    if storage_tier not in STORAGE_TIERS:
        raise ValueError(f"unsupported storage tier: {storage_tier}")
    token_count = int(data.get("token_count") or 0)
    bytes_size = int(data.get("bytes") or 0)
    ttl_seconds = max(30, min(int(data.get("ttl_seconds") or 3600), 604_800))
    if token_count <= 0:
        raise ValueError("token_count must be positive")
    if bytes_size < 0:
        raise ValueError("bytes must be non-negative")

    compatibility = dict(data.get("compatibility") or {})
    compatibility.setdefault("model_id", model_id)
    compatibility.setdefault("runtime", runtime)
    fingerprint = compatibility_fingerprint(compatibility)
    supplied = str(data.get("compatibility_fingerprint") or "")
    if supplied and not hmac.compare_digest(supplied, fingerprint):
        raise ValueError("compatibility_fingerprint does not match compatibility")

    capabilities = runtime_capabilities(runtime, data.get("capabilities"))
    portable = bool(data.get("portable")) and capabilities["export_restore"]
    if storage_tier == "distributed" and not capabilities["distributed_restore"]:
        raise ValueError("runtime does not advertise distributed cache restore")

    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "cache_id": str(data.get("cache_id") or f"kvc-{uuid.uuid4().hex}"),
        "prefix_id": prefix_id,
        "node_id": node_id,
        "endpoint_id": endpoint_id,
        "model_id": model_id,
        "model_quantization": compatibility.get("model_quantization"),
        "kv_k_quantization": compatibility.get("kv_k_quantization"),
        "kv_v_quantization": compatibility.get("kv_v_quantization"),
        "runtime": runtime,
        "runtime_version": compatibility.get("runtime_version"),
        "compatibility_fingerprint": fingerprint,
        "compatibility": compatibility,
        "privacy_scope": privacy_scope,
        "scope_id": _required_text(data.get("scope_id"), "scope_id"),
        "token_count": token_count,
        "bytes": bytes_size,
        "storage_tier": storage_tier,
        "artifact_ref": str(data.get("artifact_ref") or "") or None,
        "portable": portable,
        "capabilities": capabilities,
        "status": "READY",
        "created_at_ts": now,
        "updated_at_ts": now,
        "last_used_at_ts": now,
        "expires_at_ts": now + ttl_seconds * 1000,
        "hit_count": 0,
        "miss_count": 0,
    }


def cache_match(
    request: Mapping[str, Any],
    candidate: Mapping[str, Any],
    manifests: Iterable[Mapping[str, Any]],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    prefix_id = str(request.get("prefix_id") or "")
    if not prefix_id:
        return {"mode": "miss", "reason": "no_prefix_identity"}
    model_id = str(candidate.get("model_id") or "")
    node_id = str(candidate.get("node_id") or "")
    expected_fingerprint = str(request.get("compatibility_fingerprint") or "")
    privacy_scope = str(request.get("privacy_scope") or "private")
    scope_id = str(request.get("scope_id") or "")
    compatible: list[Mapping[str, Any]] = []
    for manifest in manifests:
        if str(manifest.get("prefix_id") or "") != prefix_id:
            continue
        if str(manifest.get("model_id") or "") != model_id:
            continue
        if str(manifest.get("privacy_scope") or "") != privacy_scope:
            continue
        if str(manifest.get("scope_id") or "") != scope_id:
            continue
        if str(manifest.get("status") or "") not in ACTIVE_CACHE_STATUSES:
            continue
        if int(manifest.get("expires_at_ts") or 0) <= now:
            continue
        if expected_fingerprint and not hmac.compare_digest(
            str(manifest.get("compatibility_fingerprint") or ""),
            expected_fingerprint,
        ):
            continue
        compatible.append(manifest)
    local = next(
        (item for item in compatible if str(item.get("node_id") or "") == node_id),
        None,
    )
    if local:
        return {"mode": "local", "manifest": dict(local)}
    distributed = next(
        (
            item
            for item in compatible
            if item.get("portable")
            and str(item.get("storage_tier") or "") == "distributed"
            and bool((item.get("capabilities") or {}).get("distributed_restore"))
        ),
        None,
    )
    if distributed:
        return {"mode": "restore", "manifest": dict(distributed)}
    return {
        "mode": "miss",
        "reason": "compatible_cache_not_resident",
        "compatible_locations": sorted(
            {str(item.get("node_id")) for item in compatible}
        ),
    }


def cache_value(
    match: Mapping[str, Any],
    *,
    tokens_per_second: float,
    transfer_mib_per_second: float = 100.0,
) -> dict[str, Any]:
    manifest = match.get("manifest") or {}
    mode = str(match.get("mode") or "miss")
    tokens = max(0, int(manifest.get("token_count") or 0))
    prefill_rate = max(1.0, float(tokens_per_second or 1.0))
    prefill_seconds = tokens / prefill_rate
    transfer_seconds = 0.0
    if mode == "restore":
        transfer_seconds = (
            max(0, int(manifest.get("bytes") or 0))
            / (1024 * 1024)
            / max(1.0, transfer_mib_per_second)
        )
    saved_seconds = max(0.0, prefill_seconds - transfer_seconds)
    # Bounded so cache locality improves a placement but cannot overwhelm
    # health, capability, or measured quality.
    bonus = min(0.18, saved_seconds / 120.0 * 0.18)
    if mode == "miss":
        bonus = 0.0
    return {
        "mode": mode,
        "prefill_seconds": round(prefill_seconds, 3),
        "restore_seconds": round(transfer_seconds, 3),
        "seconds_saved": round(saved_seconds, 3),
        "score_bonus": round(bonus, 4),
        "cache_id": manifest.get("cache_id"),
    }
