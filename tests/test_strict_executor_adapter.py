from __future__ import annotations

import base64
import json
import os
import time
from types import SimpleNamespace

import pytest

from assistx.agents import hermes_agent_adapter as hermes

from assistx import strict_executor_adapter


class _Client:
    pass


class _ImmediateProcess:
    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = 12345
        self.returncode = 0

    def poll(self):
        return 0

    def communicate(self):
        return "Session ID: hermes-session-1\nresult", ""


def _adapter():
    return SimpleNamespace(
        ASSISTX_URL="http://assistx.test",
        AGENT_ID="hermes-test",
        AGENT_CAPABILITIES=["terminal", "file"],
        HERMES_BIN="hermes",
        HERMES_MODEL="auto/code",
        HERMES_PROVIDER="assistx-router",
        HERMES_TIMEOUT=30,
        AssistXClient=_Client,
        run_hermes=lambda *_args, **_kwargs: {"success": False},
        call_self_task_llm=lambda *_args, **_kwargs: {"success": True},
    )


def test_task_token_is_child_process_only(monkeypatch):
    monkeypatch.setenv("ASSISTX_STRICT_EXECUTOR_AUTH", "true")
    monkeypatch.setenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "bootstrap-secret")
    monkeypatch.setenv("ASSISTX_EXECUTOR_SERVICE_TOKEN", "router-service-secret")
    monkeypatch.setenv("AUTO_ROUTER_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "assistx-internal-service-token")
    created = {}

    def popen(cmd, **kwargs):
        process = _ImmediateProcess(cmd, **kwargs)
        created["process"] = process
        return process

    monkeypatch.setattr(strict_executor_adapter.subprocess, "Popen", popen)
    adapter = _adapter()
    strict_executor_adapter.install_strict_executor_adapter(adapter)
    client = adapter.AssistXClient()
    client.task_token = "claim-scoped-token"
    client.task_claims = {
        "task_id": "task-1",
        "claim_id": "claim-1",
        "agent_id": "hermes-test",
    }

    result = adapter.run_hermes("do the bounded task")
    child_env = created["process"].kwargs["env"]

    assert result["success"] is True
    assert result["session_id"] == "hermes-session-1"
    assert os.environ["OPENAI_API_KEY"] == "assistx-internal-service-token"
    assert child_env["OPENAI_API_KEY"] == "claim-scoped-token"
    assert child_env["HERMES_EXECUTOR_TOKEN"] == "claim-scoped-token"
    assert "ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN" not in child_env
    assert "ASSISTX_EXECUTOR_SERVICE_TOKEN" not in child_env
    assert "AUTO_ROUTER_ADMIN_TOKEN" not in child_env


def test_lease_loss_blocks_new_hermes_process(monkeypatch):
    monkeypatch.setenv("ASSISTX_STRICT_EXECUTOR_AUTH", "true")
    monkeypatch.setenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "bootstrap-secret")
    adapter = _adapter()
    strict_executor_adapter.install_strict_executor_adapter(adapter)
    client = adapter.AssistXClient()
    client.task_token = "claim-scoped-token"
    client._lease_lost.set()

    result = adapter.run_hermes("must not start")
    assert result["success"] is False
    assert result["error"] == "executor_lease_lost"


def _synthetic_jwt(aliases, *, label="scoped"):
    """Non-cryptographic fixture; only the real router validates signatures."""
    def enc(value):
        return base64.urlsafe_b64encode(
            json.dumps(value).encode("utf-8"),
        ).rstrip(b"=").decode("ascii")
    return f"{enc({'alg': 'EdDSA', 'typ': 'JWT'})}.{enc({'allowed_model_aliases': aliases, 'label': label})}.synthetic"


def _strict_self_task(monkeypatch, *, enabled=True, model_alias="local/approved"):
    """Install the real direct-call path with all network requests stubbed."""
    monkeypatch.setenv("ASSISTX_STRICT_EXECUTOR_AUTH", "true")
    monkeypatch.setenv("ASSISTX_EXECUTOR_BOOTSTRAP_TOKEN", "synthetic-bootstrap")
    monkeypatch.setenv("HERMES_SELFTASK_ENABLED", "true" if enabled else "false")
    monkeypatch.setattr(hermes, "fleet", None)
    selected = _adapter()
    selected.call_self_task_llm = hermes.call_self_task_llm
    strict_executor_adapter.install_strict_executor_adapter(selected)
    client = selected.AssistXClient()
    token = _synthetic_jwt([model_alias])
    client.task_token = token
    client.task_claims = {
        "task_id": "task-1",
        "claim_id": "claim-1",
        "agent_id": selected.AGENT_ID,
        "exp": int(time.time()) + 300,
        "allowed_model_aliases": [model_alias],
    }
    client._active_task_id = "task-1"
    client._active_claim_id = "claim-1"
    client._active_session_id = "session-1"
    return selected, client, token


def test_default_disabled_direct_selftask_never_sends_request(monkeypatch):
    selected, _, _ = _strict_self_task(monkeypatch, enabled=False)
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_kw: pytest.fail("outbound request"))
    result = selected.call_self_task_llm("synthetic", "local/approved")
    assert result["error"] == "self_tasks_disabled_in_strict_executor"


def test_enabled_selftask_requires_claim_and_never_falls_back(monkeypatch):
    selected, client, _ = _strict_self_task(monkeypatch)
    client.task_token = ""
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "never-use-me")
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_kw: pytest.fail("outbound request"))
    result = selected.call_self_task_llm("synthetic", "local/approved")
    assert result["error"] == "self_task_requires_active_claim"


def test_strict_selftask_uses_exact_active_token_not_parent_env_or_supplied_token(monkeypatch):
    selected, _, expected = _strict_self_task(monkeypatch)
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "privileged-service-secret")
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", _synthetic_jwt(["unapproved/model"]))
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "synthetic-response"}}],
                "usage": {"completion_tokens": 1},
            }

    def intercepted_post(_url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(hermes.requests, "post", intercepted_post)
    result = selected.call_self_task_llm(
        "synthetic", "local/approved", executor_token=_synthetic_jwt(["bad/model"]),
    )
    assert result["success"] is True
    assert result["output"] == "synthetic-response"
    assert captured["headers"] == {"Authorization": f"Bearer {expected}"}
    assert captured["json"]["model"] == "local/approved"
    assert "privileged-service-secret" not in repr(captured)


@pytest.mark.parametrize("field,value,reason", [
    ("task_id", "other", "self_task_claim_identity_or_expiry_invalid"),
    ("claim_id", "old-claim", "self_task_claim_identity_or_expiry_invalid"),
    ("agent_id", "other-agent", "self_task_claim_identity_or_expiry_invalid"),
    ("exp", 0, "self_task_claim_identity_or_expiry_invalid"),
    ("exp", "not-integer", "self_task_claim_identity_or_expiry_invalid"),
    ("exp", True, "self_task_claim_identity_or_expiry_invalid"),
    ("allowed_model_aliases", ["other/model"], "self_task_model_outside_claim_scope"),
    ("allowed_model_aliases", [], "self_task_model_outside_claim_scope"),
    ("allowed_model_aliases", None, "self_task_model_outside_claim_scope"),
])
def test_selftask_denies_bad_claim_or_model_before_http(monkeypatch, field, value, reason):
    selected, client, _ = _strict_self_task(monkeypatch)
    client.task_claims[field] = value
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "do-not-forward")
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_kw: pytest.fail("outbound request"))
    result = selected.call_self_task_llm("synthetic", "local/approved")
    assert result["success"] is False
    assert result["error"] == reason


def test_selftask_denies_lease_loss_and_missing_active_session_before_http(monkeypatch):
    selected, client, _ = _strict_self_task(monkeypatch)
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_kw: pytest.fail("outbound request"))
    client._lease_lost.set()
    assert selected.call_self_task_llm("synthetic", "local/approved")["error"] == (
        "self_task_requires_active_claim"
    )
    client._lease_lost.clear()
    client._active_session_id = ""
    assert selected.call_self_task_llm("synthetic", "local/approved")["error"] == (
        "self_task_claim_identity_or_expiry_invalid"
    )


def test_direct_call_explicit_empty_credential_never_falls_back_to_env(monkeypatch):
    token = _synthetic_jwt(["local/approved"])
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", token)
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "privileged-service-secret")
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_kw: pytest.fail("outbound request"))
    result = hermes.call_self_task_llm(
        "synthetic", "local/approved", executor_token="",
    )
    assert result["success"] is False
    assert result["error"] == "claim_scoped_executor_token_required"


def test_strict_selftask_uses_current_model_scope_not_global_model_allowlist(monkeypatch):
    selected, _, _ = _strict_self_task(monkeypatch)
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", _synthetic_jwt(["remote/unapproved"]))
    monkeypatch.setattr(hermes.requests, "post", lambda *_a, **_kw: pytest.fail("outbound request"))
    result = selected.call_self_task_llm("synthetic", "remote/unapproved")
    assert result["error"] == "self_task_model_outside_claim_scope"
