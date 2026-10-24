"""No-network direct and aggregate provider egress denial, no env escape."""
from __future__ import annotations

import pytest
from assistx.llm import client as llm


@pytest.fixture(autouse=True)
def forbid_effects(monkeypatch):
    """Fail if a denied path accesses any actual network or discovery."""
    def forbidden(*a, **kw):
        pytest.fail("denied path attempted network or discovery")
    monkeypatch.setattr(llm.requests, "post", forbidden)
    monkeypatch.setattr(llm.requests, "get", forbidden)
    monkeypatch.setattr(llm, "_candidate_models", forbidden)
    monkeypatch.setattr(llm, "_fleet_model_inventory", forbidden)
    monkeypatch.setattr(llm, "fleet_base_urls_for", forbidden)


@pytest.mark.parametrize("name,invoke", [
    ("direct-openai", lambda: llm._chat_openai([{"role": "user", "content": "fixture"}], "fixture", False)),
    ("direct-ollama", lambda: llm._chat_ollama([{"role": "user", "content": "fixture"}], "fixture", False)),
    ("aggregate", lambda: llm.chat([{"role": "user", "content": "fixture"}], model="fixture")),
    ("json", lambda: llm.tool_json([{"role": "user", "content": "fixture"}])),
])
def test_chat_and_fallback_denied_before_discovery(name, invoke):
    with pytest.raises(RuntimeError, match="resource_owned_quota_effect_authority_unverified"):
        invoke()


@pytest.mark.parametrize("fn", [
    lambda: llm.stream_chat([{"role": "user", "content": "fixture"}], "fixture"),
    lambda: llm._stream_ollama([{"role": "user", "content": "fixture"}], "fixture"),
    lambda: llm._stream_openai([{"role": "user", "content": "fixture"}], "fixture"),
    lambda: llm._stream_openai_fleet([{"role": "user", "content": "fixture"}], "fixture", "http://bad.invalid/v1"),
])
def test_streaming_direct_helpers_yield_denial_without_network(fn):
    events = list(fn())
    assert events == [{"event": "error", "data": {
        "error": "resource_owned_quota_effect_authority_unverified"}}]


@pytest.mark.parametrize("backend", ["ollama", "openai"])
def test_embedding_denied_no_fallback_post(monkeypatch, backend):
    monkeypatch.setattr(llm, "LLM_BACKEND", backend)
    assert llm.embed("embedding fixture text") is None


def test_no_environment_approval_bypass(monkeypatch):
    for name in ("ASSISTX_INTERNAL_SERVICE_TOKEN", "HERMES_EXECUTOR_TOKEN",
                 "FLEET_APPROVED", "ALLOW_LIVE_PROVIDER", "QUOTA_AUTHORIZED"):
        monkeypatch.setenv(name, "approved-by-caller")
    with pytest.raises(RuntimeError, match="resource_owned_quota_effect_authority_unverified"):
        llm._chat_openai([], "fixture", False)
    assert list(llm.stream_chat([], "fixture"))[0]["event"] == "error"
    assert llm.embed("fixture") is None


def test_no_unguarded_direct_stream_post_source():
    """Pin every known request helper to the same fail-closed effect gate."""
    import inspect
    for name in ("_chat_openai", "_chat_ollama", "chat", "stream_chat",
                 "_stream_ollama", "_stream_openai", "_stream_openai_fleet", "embed"):
        src = inspect.getsource(getattr(llm, name))
        assert "_require_effect_authority(" in src, name
    src = inspect.getsource(llm._require_effect_authority)
    assert "raise RuntimeError" in src
