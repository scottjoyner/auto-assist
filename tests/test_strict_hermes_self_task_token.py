"""Hermetic claim-only strict self-task tests; no real router access."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from assistx.agents import hermes_agent_adapter as hermes
from assistx import strict_executor_adapter as strict


def _wrapped(monkeypatch):
    monkeypatch.setenv("ASSISTX_STRICT_EXECUTOR_AUTH", "true")
    monkeypatch.setenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "synthetic-bootstrap")
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "privileged-must-never-use")
    monkeypatch.delenv("HERMES_EXECUTOR_TOKEN", raising=False)
    monkeypatch.setenv("HERMES_SELFTASK_ENABLED", "true")
    adapter = SimpleNamespace(
        ASSISTX_URL="http://synthetic.invalid",
        AGENT_ID="agent-1", AGENT_CAPABILITIES=[],
        HERMES_BIN="unused", HERMES_MODEL="auto/code",
        HERMES_PROVIDER="unused", HERMES_TIMEOUT=1,
        AssistXClient=type("SyntheticClient", (), {}),
        run_hermes=lambda *_args, **_kw: None,
        call_self_task_llm=hermes.call_self_task_llm,
    )
    strict.install_strict_executor_adapter(adapter)
    client = adapter.AssistXClient()
    client.task_token = "fake.header.signature"
    client._active_task_id = "t-1"
    client._active_claim_id = "c-1"
    client.task_claims = {
        "task_id": "t-1", "claim_id": "c-1", "agent_id": "agent-1",
        "exp": int(time.time()) + 100,
        "allowed_model_aliases": ["local/approved"],
    }
    return adapter, client


def test_valid_claim_passes_only_claim_token_to_http(monkeypatch):
    adapter, _ = _wrapped(monkeypatch)
    observed = {}
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "fixture"}}]}
    def post(_url, **kwargs):
        observed.update(kwargs)
        return FakeResponse()
    monkeypatch.setattr(hermes.requests, "post", post)
    result = adapter.call_self_task_llm("synthetic prompt", "local/approved")
    assert result["success"] is True
    assert observed["headers"] == {"Authorization": "Bearer fake.header.signature"}
    assert "privileged-must-never-use" not in repr(observed)


@pytest.mark.parametrize("mutation", ["expired", "invalid_exp", "wrong_claim", "wrong_agent", "no_token", "lease_lost"])
def test_invalid_claim_never_sends_http(monkeypatch, mutation):
    adapter, client = _wrapped(monkeypatch)
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_k: pytest.fail("HTTP must not be called"))
    if mutation == "expired":
        client.task_claims["exp"] = int(time.time()) - 1
    elif mutation == "invalid_exp":
        client.task_claims["exp"] = "not-a-timestamp"
    elif mutation == "wrong_claim":
        client.task_claims["claim_id"] = "not-the-active-claim"
    elif mutation == "wrong_agent":
        client.task_claims["agent_id"] = "other-agent"
    elif mutation == "no_token":
        client.task_token = ""
    else:
        client._lease_lost.set()
    assert adapter.call_self_task_llm("fixture", "local/approved")["success"] is False


def test_claim_scope_denies_model_even_with_internal_token(monkeypatch):
    adapter, _ = _wrapped(monkeypatch)
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_k: pytest.fail("HTTP must not be called"))
    result = adapter.call_self_task_llm("fixture", "remote/other")
    assert result["error"] == "model_outside_executor_claim_scope"


def test_self_tasks_default_off(monkeypatch):
    adapter, _ = _wrapped(monkeypatch)
    monkeypatch.setenv("HERMES_SELFTASK_ENABLED", "false")
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_k: pytest.fail("HTTP must not be called"))
    assert adapter.call_self_task_llm("fixture", "local/approved")["error"] == "self_tasks_disabled_in_strict_executor"
