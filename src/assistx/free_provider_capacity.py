"""Read-only provider capacity metadata and state projection.

This module never reads credential values and never performs network I/O. It
describes independent free/free-tier pools so the supervisor can reason about
what is configured and what runtime evidence means without enabling paid
fallback or changing routing authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


FREE_SEMANTICS = frozenset(
    {
        "anonymous_free",
        "recurring_free_tier",
        "trial_credits",
        "reserve_only",
        "known_payment_required",
        "unknown",
    }
)

OBSERVED_STATES = frozenset(
    {
        "unqualified",
        "usable",
        "rate_limited",
        "quota_exhausted",
        "payment_required",
        "provider_overloaded",
        "auth_error",
        "unknown",
    }
)


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    endpoint_family: str
    base_url_template: str
    catalog_url: str | None
    credential_vars: tuple[str, ...]
    free_semantics: str
    exact_model_support: bool
    tool_capability_discovery: str
    automatic_paid_fallback_forbidden: bool = True
    requires_account_id: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if self.free_semantics not in FREE_SEMANTICS:
            raise ValueError(f"unknown free semantics: {self.free_semantics}")


PROVIDER_SPECS: Mapping[str, ProviderSpec] = {
    "kilo_anonymous": ProviderSpec(
        provider_id="kilo_anonymous",
        endpoint_family="openai-compatible",
        base_url_template="https://api.kilo.ai/api/openrouter/chat/completions",
        catalog_url="https://api.kilo.ai/api/openrouter/models",
        credential_vars=(),
        free_semantics="anonymous_free",
        exact_model_support=True,
        tool_capability_discovery="catalog-supported_parameters+exact-tool-canary",
        notes="Literal anonymous bearer; exact model IDs required for substantive work.",
    ),
    "nvidia_nim": ProviderSpec(
        provider_id="nvidia_nim",
        endpoint_family="openai-compatible",
        base_url_template="https://integrate.api.nvidia.com/v1/chat/completions",
        catalog_url="https://integrate.api.nvidia.com/v1/models",
        credential_vars=("NVIDIA_API_KEY", "NGC_API_KEY"),
        free_semantics="recurring_free_tier",
        exact_model_support=True,
        tool_capability_discovery="models-metadata+exact-tool-canary",
    ),
    "groq": ProviderSpec(
        provider_id="groq",
        endpoint_family="openai-compatible",
        base_url_template="https://api.groq.com/openai/v1/chat/completions",
        catalog_url="https://api.groq.com/openai/v1/models",
        credential_vars=("GROQ_API_KEY",),
        free_semantics="recurring_free_tier",
        exact_model_support=True,
        tool_capability_discovery="models-list+exact-tool-canary",
    ),
    "gemini_api": ProviderSpec(
        provider_id="gemini_api",
        endpoint_family="gemini-native",
        base_url_template="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        catalog_url="https://generativelanguage.googleapis.com/v1beta/models",
        credential_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        free_semantics="recurring_free_tier",
        exact_model_support=True,
        tool_capability_discovery="models-capabilities+function-call-canary",
    ),
    "mistral": ProviderSpec(
        provider_id="mistral",
        endpoint_family="openai-compatible",
        base_url_template="https://api.mistral.ai/v1/chat/completions",
        catalog_url="https://api.mistral.ai/v1/models",
        credential_vars=("MISTRAL_API_KEY",),
        free_semantics="recurring_free_tier",
        exact_model_support=True,
        tool_capability_discovery="models-list+exact-tool-canary",
    ),
    "cloudflare_workers_ai": ProviderSpec(
        provider_id="cloudflare_workers_ai",
        endpoint_family="openai-compatible",
        base_url_template="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions",
        catalog_url=None,
        credential_vars=("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"),
        free_semantics="recurring_free_tier",
        exact_model_support=True,
        tool_capability_discovery="workers-ai-catalog+exact-tool-canary",
        requires_account_id=True,
    ),
    "ollama_cloud": ProviderSpec(
        provider_id="ollama_cloud",
        endpoint_family="ollama",
        base_url_template="https://ollama.com/api/chat",
        catalog_url="https://ollama.com/api/tags",
        credential_vars=("OLLAMA_API_KEY",),
        free_semantics="recurring_free_tier",
        exact_model_support=True,
        tool_capability_discovery="tags+ollama-tool-canary",
    ),
    "kilo_gateway": ProviderSpec(
        provider_id="kilo_gateway",
        endpoint_family="openai-compatible",
        base_url_template="https://api.kilo.ai/api/gateway/chat/completions",
        catalog_url="https://api.kilo.ai/api/gateway/models",
        credential_vars=("KILO_API_KEY",),
        free_semantics="recurring_free_tier",
        exact_model_support=True,
        tool_capability_discovery="gateway-models+exact-tool-canary",
    ),
    "poolside": ProviderSpec(
        provider_id="poolside",
        endpoint_family="openai-compatible",
        base_url_template="https://inference.poolside.ai/v1/chat/completions",
        catalog_url="https://inference.poolside.ai/v1/models",
        credential_vars=("POOLSIDE_API_KEY",),
        free_semantics="recurring_free_tier",
        exact_model_support=True,
        tool_capability_discovery="models-list+exact-tool-canary",
    ),
    "byteplus": ProviderSpec(
        provider_id="byteplus",
        endpoint_family="openai-compatible-coding",
        base_url_template="https://ark.ap-southeast.bytepluses.com/api/coding/v3/chat/completions",
        catalog_url=None,
        credential_vars=("BYTEPLUS_API_KEY",),
        free_semantics="trial_credits",
        exact_model_support=True,
        tool_capability_discovery="account-catalog+exact-tool-canary",
    ),
    "zai": ProviderSpec(
        provider_id="zai",
        endpoint_family="openai-compatible",
        base_url_template="https://api.z.ai/api/paas/v4/chat/completions",
        catalog_url=None,
        credential_vars=("ZAI_API_KEY",),
        free_semantics="reserve_only",
        exact_model_support=True,
        tool_capability_discovery="existing-qualified-traces",
        notes="Reserve-only by policy; never auto-selected by this module.",
    ),
    "cerebras": ProviderSpec(
        provider_id="cerebras",
        endpoint_family="openai-compatible",
        base_url_template="https://api.cerebras.ai/v1/chat/completions",
        catalog_url="https://api.cerebras.ai/v1/models",
        credential_vars=("CEREBRAS_API_KEY",),
        free_semantics="known_payment_required",
        exact_model_support=True,
        tool_capability_discovery="models-list+exact-tool-canary",
        notes="Current fleet credential returned HTTP 402 payment required.",
    ),
}


def is_selectable(spec: ProviderSpec) -> bool:
    """Whether the provider may enter automatic free-pool selection."""
    return spec.free_semantics not in {"reserve_only", "known_payment_required", "unknown"}


def credential_configured(
    spec: ProviderSpec,
    credential_presence: Mapping[str, bool],
) -> bool:
    """Check credential presence using variable names only, never values.

    Most providers accept any one of their documented key aliases. Cloudflare
    Workers AI is different: it needs both an API token and an account id.
    """
    if not spec.credential_vars:
        return True
    if spec.requires_account_id:
        return all(bool(credential_presence.get(name)) for name in spec.credential_vars)
    return any(bool(credential_presence.get(name)) for name in spec.credential_vars)


def project_capacity_state(
    spec: ProviderSpec,
    *,
    credential_presence: Mapping[str, bool],
    qualified: bool,
    observed_state: str | None = None,
) -> str:
    """Project provider capacity without network calls or paid fallback.

    Observed runtime evidence is more informative than catalog metadata, but it
    never makes reserve-only or known-payment-required providers selectable.
    """
    if spec.free_semantics == "reserve_only":
        return "reserve_only"
    if spec.free_semantics == "known_payment_required":
        return "payment_required"

    if not credential_configured(spec, credential_presence):
        return "unconfigured"

    if observed_state:
        if observed_state not in OBSERVED_STATES:
            return "unknown"
        if observed_state in {
            "unqualified",
            "rate_limited",
            "quota_exhausted",
            "payment_required",
            "provider_overloaded",
            "auth_error",
        }:
            return observed_state

    if not spec.exact_model_support or not qualified:
        return "unqualified"

    if observed_state == "usable":
        return "usable"

    # A qualified route with no contradictory capacity observation is usable
    # from the scheduler's read model. Live launchers still fail closed on
    # provider errors and never enable paid fallback.
    return "usable"


def provider_inventory(
    *,
    credential_presence: Mapping[str, bool],
    qualified_provider_ids: set[str] | frozenset[str] = frozenset(),
    observed_states: Mapping[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    observed_states = observed_states or {}
    inventory: dict[str, dict[str, object]] = {}
    for provider_id, spec in PROVIDER_SPECS.items():
        state = project_capacity_state(
            spec,
            credential_presence=credential_presence,
            qualified=provider_id in qualified_provider_ids,
            observed_state=observed_states.get(provider_id),
        )
        inventory[provider_id] = {
            "provider_id": provider_id,
            "endpoint_family": spec.endpoint_family,
            "base_url_template": spec.base_url_template,
            "catalog_url": spec.catalog_url,
            "credential_vars": list(spec.credential_vars),
            "free_semantics": spec.free_semantics,
            "exact_model_support": spec.exact_model_support,
            "tool_capability_discovery": spec.tool_capability_discovery,
            "automatic_paid_fallback_forbidden": spec.automatic_paid_fallback_forbidden,
            "selectable": is_selectable(spec),
            "state": state,
        }
    return inventory
