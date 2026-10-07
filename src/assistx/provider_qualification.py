"""Fail-closed planning for independent free/free-tier provider qualification.

No network I/O occurs here. Credential handling is boolean presence only; secret
values are never returned, persisted, or logged.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
import json
import os

from .free_provider_capacity import (
    PROVIDER_SPECS,
    ProviderSpec,
    credential_configured,
    is_selectable,
    project_capacity_state,
)

ROUTE_ALIAS_TOKENS = frozenset({"free", "auto", "router", "any", "best", "default"})

DEFAULT_CANDIDATE_MODELS: Mapping[str, tuple[str, ...]] = {
    "kilo_anonymous": (
        "cohere/north-mini-code:free",
        "inclusionai/ling-3.1-flash",
        "nvidia/nemotron-3-super-120b-a12b:free",
    ),
    "nvidia_nim": ("nvidia/nemotron-3-ultra-550b-a55b",),
    "groq": ("openai/gpt-oss-120b", "qwen/qwen3-32b"),
    "gemini_api": ("gemini-3.5-flash-lite", "gemini-2.5-flash"),
    "mistral": ("codestral-latest", "mistral-large-latest"),
    "cloudflare_workers_ai": (
        "@cf/zai-org/glm-4.7-flash",
        "@cf/qwen/qwen2.5-coder-32b-instruct",
    ),
    "ollama_cloud": ("gpt-oss:120b", "qwen3.5"),
    "kilo_gateway": (
        "nvidia/nemotron-3-super-120b-a12b:free",
        "kwaipilot/kat-coder-pro-v2.5:free",
    ),
    "poolside": ("poolside/laguna-s-2.1", "poolside/laguna-xs-2.1"),
    "byteplus": ("seed-2-0-code-preview-260328", "gpt-oss-120b-250805"),
    "zai": (),
    "cerebras": (),
}

SAFE_LEDGER_KEYS = frozenset({
    "schema", "provider", "requested_model", "response_model", "qualified",
    "model_identity_complete", "capacity_state", "structured_tool_call", "cost",
    "upstream_inference_cost", "started_at", "elapsed_s", "http_status",
    "finish_reason", "context_length", "supported_parameters", "tool_names",
    "tool_arguments", "provider_code", "provider_name", "limit_source", "error",
})


def route_kind(model_id: str) -> str:
    leaf = str(model_id or "").strip().rsplit("/", 1)[-1].lower().split(":", 1)[0]
    return "alias" if leaf in ROUTE_ALIAS_TOKENS else "exact"


def exact_models(models: Sequence[str]) -> list[str]:
    return sorted({str(model) for model in models if route_kind(str(model)) == "exact"})


def credential_presence(environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    env = os.environ if environ is None else environ
    names = sorted({name for spec in PROVIDER_SPECS.values() for name in spec.credential_vars})
    return {name: bool(env.get(name)) for name in names}


def _credential_summary(spec: ProviderSpec, presence: Mapping[str, bool]) -> dict[str, Any]:
    names = list(spec.credential_vars)
    return {
        "required_names": names,
        "present_names": [name for name in names if bool(presence.get(name))],
        "configured": credential_configured(spec, presence),
        "requires_all": bool(spec.requires_account_id),
    }


def build_qualification_plan(
    *,
    presence: Mapping[str, bool] | None = None,
    candidate_models: Mapping[str, Sequence[str]] | None = None,
    observed_states: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    presence = credential_presence() if presence is None else dict(presence)
    candidate_models = DEFAULT_CANDIDATE_MODELS if candidate_models is None else candidate_models
    observed_states = observed_states or {}
    rows: list[dict[str, Any]] = []
    for provider_id in sorted(PROVIDER_SPECS):
        spec = PROVIDER_SPECS[provider_id]
        creds = _credential_summary(spec, presence)
        candidates = exact_models(tuple(candidate_models.get(provider_id, ())))
        state = project_capacity_state(
            spec,
            credential_presence=presence,
            qualified=False,
            observed_state=observed_states.get(provider_id),
        )
        selectable = is_selectable(spec)
        blocked_states = {
            "rate_limited", "quota_exhausted", "payment_required",
            "provider_overloaded", "auth_error",
        }
        live_canary_allowed = bool(
            selectable and creds["configured"] and candidates and state not in blocked_states
        )
        if not selectable:
            reason = "policy_excluded"
        elif not creds["configured"]:
            reason = "unconfigured"
        elif not candidates:
            reason = "no_exact_candidate"
        elif not live_canary_allowed:
            reason = f"capacity_{state}"
        else:
            reason = "ready_for_explicit_canary"
        rows.append({
            "provider_id": provider_id,
            "endpoint_family": spec.endpoint_family,
            "base_url_template": spec.base_url_template,
            "catalog_url": spec.catalog_url,
            "free_semantics": spec.free_semantics,
            "automatic_paid_fallback_forbidden": spec.automatic_paid_fallback_forbidden,
            "exact_model_support": spec.exact_model_support,
            "tool_capability_discovery": spec.tool_capability_discovery,
            "credential": creds,
            "candidate_models": candidates,
            "capacity_state": state,
            "selectable": selectable,
            "live_canary_allowed": live_canary_allowed,
            "reason": reason,
        })
    return rows


def parse_catalog_models(provider_id: str, payload: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    if provider_id == "gemini_api":
        rows = payload.get("models")
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, Mapping) and isinstance(row.get("name"), str):
                values.append(str(row["name"]).removeprefix("models/"))
        return exact_models(values)
    if provider_id == "ollama_cloud":
        rows = payload.get("models")
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, Mapping):
                value = row.get("model") or row.get("name")
                if isinstance(value, str):
                    values.append(value)
        return exact_models(values)
    if provider_id == "cloudflare_workers_ai":
        rows = payload.get("result")
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, Mapping):
                value = row.get("name") or row.get("id")
                if isinstance(value, str):
                    values.append(value)
        return exact_models(values)
    rows = payload.get("data")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, Mapping):
            value = row.get("id") or row.get("name")
            if isinstance(value, str):
                values.append(value)
    return exact_models(values)


def response_model(provider_id: str, payload: Mapping[str, Any]) -> str | None:
    value = payload.get("modelVersion") if provider_id == "gemini_api" else payload.get("model")
    return str(value).removeprefix("models/") if isinstance(value, str) and value else None


def model_identity_complete(provider_id: str, requested_model: str, payload: Mapping[str, Any]) -> bool:
    if route_kind(requested_model) != "exact":
        return False
    return response_model(provider_id, payload) == requested_model.removeprefix("models/")


def sanitize_qualification_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in SAFE_LEDGER_KEYS if key in record}


def append_qualification_record(ledger_path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    safe = sanitize_qualification_record(record)
    safe.setdefault("schema", "free-provider-qualification/v1")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(ledger_path.parent, 0o700)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(ledger_path, 0o600)
    return safe


def qualification_plan_document(
    *,
    presence: Mapping[str, bool] | None = None,
    observed_states: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "free-provider-qualification-plan/v1",
        "network_calls_performed": False,
        "zai_policy": "reserve_only",
        "providers": build_qualification_plan(
            presence=presence,
            observed_states=observed_states,
        ),
    }
