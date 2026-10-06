from src.assistx.free_provider_capacity import (
    PROVIDER_SPECS,
    ProviderSpec,
    credential_configured,
    is_selectable,
    project_capacity_state,
    provider_inventory,
)


def test_specs_cover_independent_pools_and_reserve_boundaries():
    expected = {
        "kilo_anonymous",
        "nvidia_nim",
        "groq",
        "gemini_api",
        "mistral",
        "cloudflare_workers_ai",
        "ollama_cloud",
        "kilo_gateway",
        "poolside",
        "byteplus",
        "zai",
        "cerebras",
    }
    assert set(PROVIDER_SPECS) == expected
    assert PROVIDER_SPECS["zai"].free_semantics == "reserve_only"
    assert PROVIDER_SPECS["cerebras"].free_semantics == "known_payment_required"
    assert not is_selectable(PROVIDER_SPECS["zai"])
    assert not is_selectable(PROVIDER_SPECS["cerebras"])


def test_endpoints_match_current_provider_families():
    assert PROVIDER_SPECS["nvidia_nim"].base_url_template == (
        "https://integrate.api.nvidia.com/v1/chat/completions"
    )
    assert PROVIDER_SPECS["groq"].catalog_url == "https://api.groq.com/openai/v1/models"
    assert PROVIDER_SPECS["mistral"].base_url_template == (
        "https://api.mistral.ai/v1/chat/completions"
    )
    assert PROVIDER_SPECS["ollama_cloud"].catalog_url == "https://ollama.com/api/tags"
    assert "{account_id}" in PROVIDER_SPECS["cloudflare_workers_ai"].base_url_template


def test_kilo_anonymous_requires_no_secret():
    spec = PROVIDER_SPECS["kilo_anonymous"]
    assert spec.credential_vars == ()
    assert credential_configured(spec, {}) is True
    assert project_capacity_state(
        spec,
        credential_presence={},
        qualified=True,
        observed_state="usable",
    ) == "usable"


def test_missing_required_credential_is_unconfigured():
    spec = PROVIDER_SPECS["groq"]
    assert credential_configured(spec, {}) is False
    assert project_capacity_state(
        spec,
        credential_presence={},
        qualified=True,
    ) == "unconfigured"


def test_key_aliases_are_alternatives_not_cumulative_requirements():
    nvidia = PROVIDER_SPECS["nvidia_nim"]
    assert credential_configured(nvidia, {"NVIDIA_API_KEY": True})
    assert credential_configured(nvidia, {"NGC_API_KEY": True})
    assert not credential_configured(nvidia, {})

    gemini = PROVIDER_SPECS["gemini_api"]
    assert credential_configured(gemini, {"GEMINI_API_KEY": True})
    assert credential_configured(gemini, {"GOOGLE_API_KEY": True})
    assert not credential_configured(gemini, {})


def test_cloudflare_requires_token_and_account_id():
    spec = PROVIDER_SPECS["cloudflare_workers_ai"]
    assert not credential_configured(spec, {"CLOUDFLARE_API_TOKEN": True})
    assert credential_configured(
        spec,
        {
            "CLOUDFLARE_API_TOKEN": True,
            "CLOUDFLARE_ACCOUNT_ID": True,
        },
    )


def test_configured_but_unqualified_provider_fails_closed():
    spec = PROVIDER_SPECS["groq"]
    assert project_capacity_state(
        spec,
        credential_presence={"GROQ_API_KEY": True},
        qualified=False,
    ) == "unqualified"


def test_observed_runtime_failures_are_preserved():
    spec = PROVIDER_SPECS["groq"]
    presence = {"GROQ_API_KEY": True}
    for state in (
        "rate_limited",
        "quota_exhausted",
        "payment_required",
        "provider_overloaded",
        "auth_error",
    ):
        assert project_capacity_state(
            spec,
            credential_presence=presence,
            qualified=True,
            observed_state=state,
        ) == state


def test_paid_fallback_policy_does_not_mean_payment_required():
    spec = PROVIDER_SPECS["nvidia_nim"]
    assert spec.automatic_paid_fallback_forbidden is True
    assert project_capacity_state(
        spec,
        credential_presence={"NVIDIA_API_KEY": True},
        qualified=True,
        observed_state="usable",
    ) == "usable"


def test_reserve_and_known_payment_required_ignore_apparent_qualification():
    assert project_capacity_state(
        PROVIDER_SPECS["zai"],
        credential_presence={"ZAI_API_KEY": True},
        qualified=True,
        observed_state="usable",
    ) == "reserve_only"
    assert project_capacity_state(
        PROVIDER_SPECS["cerebras"],
        credential_presence={"CEREBRAS_API_KEY": True},
        qualified=True,
        observed_state="usable",
    ) == "payment_required"


def test_inventory_contains_names_only_and_states():
    inv = provider_inventory(
        credential_presence={"GROQ_API_KEY": True},
        qualified_provider_ids={"kilo_anonymous"},
        observed_states={"kilo_anonymous": "usable"},
    )
    assert inv["kilo_anonymous"]["state"] == "usable"
    assert inv["groq"]["state"] == "unqualified"
    assert inv["zai"]["state"] == "reserve_only"
    assert inv["cerebras"]["state"] == "payment_required"
    assert inv["groq"]["credential_vars"] == ["GROQ_API_KEY"]
    assert "credential_value" not in inv["groq"]
