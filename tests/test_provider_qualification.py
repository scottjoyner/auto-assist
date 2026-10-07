import json

from src.assistx.provider_qualification import (
    append_qualification_record,
    build_qualification_plan,
    credential_presence,
    exact_models,
    model_identity_complete,
    parse_catalog_models,
    qualification_plan_document,
    route_kind,
)


def _by_id(rows):
    return {row["provider_id"]: row for row in rows}


def test_credential_presence_returns_booleans_only():
    presence = credential_presence({
        "GROQ_API_KEY": "super-secret",
        "CLOUDFLARE_API_TOKEN": "token",
        "CLOUDFLARE_ACCOUNT_ID": "account",
    })
    assert presence["GROQ_API_KEY"] is True
    assert all(isinstance(value, bool) for value in presence.values())
    assert "super-secret" not in json.dumps(presence)


def test_unconfigured_providers_fail_closed():
    rows = _by_id(build_qualification_plan(presence={}))
    assert rows["groq"]["capacity_state"] == "unconfigured"
    assert rows["groq"]["live_canary_allowed"] is False
    assert rows["nvidia_nim"]["reason"] == "unconfigured"


def test_cloudflare_requires_token_and_account_id():
    token_only = _by_id(build_qualification_plan(presence={"CLOUDFLARE_API_TOKEN": True}))
    assert token_only["cloudflare_workers_ai"]["credential"]["configured"] is False
    both = _by_id(build_qualification_plan(presence={
        "CLOUDFLARE_API_TOKEN": True,
        "CLOUDFLARE_ACCOUNT_ID": True,
    }))
    assert both["cloudflare_workers_ai"]["credential"]["configured"] is True
    assert both["cloudflare_workers_ai"]["live_canary_allowed"] is True


def test_reserve_and_payment_required_are_never_canary_eligible():
    rows = _by_id(build_qualification_plan(
        presence={"ZAI_API_KEY": True, "CEREBRAS_API_KEY": True}
    ))
    assert rows["zai"]["selectable"] is False
    assert rows["zai"]["live_canary_allowed"] is False
    assert rows["cerebras"]["capacity_state"] == "payment_required"
    assert rows["cerebras"]["live_canary_allowed"] is False


def test_exact_model_requirement_filters_router_aliases():
    assert route_kind("openrouter/free") == "alias"
    assert route_kind("auto") == "alias"
    assert exact_models(["openrouter/free", "model/x", "model/x"]) == ["model/x"]


def test_plan_is_deterministic():
    first = build_qualification_plan(presence={"GROQ_API_KEY": True})
    second = build_qualification_plan(presence={"GROQ_API_KEY": True})
    assert first == second


def test_catalog_parsers_and_identity():
    assert parse_catalog_models(
        "groq", {"data": [{"id": "openai/gpt-oss-120b"}, {"id": "auto"}]}
    ) == ["openai/gpt-oss-120b"]
    assert model_identity_complete(
        "groq", "openai/gpt-oss-120b", {"model": "openai/gpt-oss-120b"}
    )
    assert parse_catalog_models(
        "gemini_api", {"models": [{"name": "models/gemini-2.5-flash"}]}
    ) == ["gemini-2.5-flash"]
    assert model_identity_complete(
        "gemini_api", "gemini-2.5-flash", {"modelVersion": "gemini-2.5-flash"}
    )
    assert parse_catalog_models(
        "ollama_cloud", {"models": [{"model": "gpt-oss:120b"}]}
    ) == ["gpt-oss:120b"]


def test_response_model_mismatch_fails_identity():
    assert not model_identity_complete(
        "groq", "openai/gpt-oss-120b", {"model": "qwen/qwen3-32b"}
    )


def test_qualification_ledger_redacts_secret_fields(tmp_path):
    ledger = tmp_path / "state" / "provider-qualification.jsonl"
    written = append_qualification_record(ledger, {
        "provider": "groq",
        "requested_model": "openai/gpt-oss-120b",
        "response_model": "openai/gpt-oss-120b",
        "qualified": True,
        "model_identity_complete": True,
        "capacity_state": "usable",
        "cost": 0,
        "upstream_inference_cost": 0,
        "api_key": "do-not-write-me",
        "authorization": "also-secret",
    })
    raw = ledger.read_text()
    assert "do-not-write-me" not in raw
    assert "also-secret" not in raw
    assert "api_key" not in written
    assert ledger.stat().st_mode & 0o777 == 0o600
    assert ledger.parent.stat().st_mode & 0o777 == 0o700


def test_plan_document_is_read_only_and_zai_reserve():
    doc = qualification_plan_document(presence={})
    assert doc["network_calls_performed"] is False
    assert doc["zai_policy"] == "reserve_only"
