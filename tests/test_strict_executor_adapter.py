from __future__ import annotations

import os
from types import SimpleNamespace

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
