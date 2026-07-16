"""Tests for the opencode-cli delegation wiring in the Hermes agent adapter.

These verify that a task routed to a configured tier is solved via Hermes's
``delegate_task(provider="opencode-cli", return_format=...)`` return-contract
path (machine-usable results) rather than a free-form ``hermes chat`` session.
"""
import importlib
import os

import pytest

import src.assistx.agents.hermes_agent_adapter as adapter


@pytest.fixture
def reload_with_delegate_tier(monkeypatch):
    """Reload the adapter with HERMES_DELEGATE_OPENCODE_TIERS=tool-small set."""
    monkeypatch.setenv("HERMES_DELEGATE_OPENCODE_TIERS", "tool-small")
    monkeypatch.setenv("HERMES_DELEGATE_RETURN_FORMAT", "verbatim")
    # point profile loader + eval registry at non-existent (writable) paths so
    # load_profiles()/load_eval() fall back to built-in defaults instead of
    # hitting /root/.hermes or /root/knowledge
    monkeypatch.setenv("HERMES_PROFILES_PATH", "/tmp/auto-assist-test-profiles.yaml")
    monkeypatch.setenv("HERMES_EVAL_PATH", "/tmp/auto-assist-test-model-profiles.json")
    importlib.reload(adapter)
    yield adapter
    # restore defaults so other tests are unaffected
    monkeypatch.delenv("HERMES_DELEGATE_OPENCODE_TIERS", raising=False)
    monkeypatch.delenv("HERMES_DELEGATE_RETURN_FORMAT", raising=False)
    importlib.reload(adapter)


def test_run_hermes_delegated_injects_delegation_toolset_and_directive(monkeypatch):
    captured = {}

    def fake_run_hermes(prompt, timeout=None, model=None, provider=None, toolsets=None):
        captured["prompt"] = prompt
        captured["toolsets"] = toolsets
        return {"success": True, "output": "PONG", "session_id": "s1", "elapsed": 1.0}

    monkeypatch.setattr(adapter, "run_hermes", fake_run_hermes)

    result = adapter.run_hermes_delegated(
        "Reply with exactly the word PONG",
        model="refinedtoolcallv5-3b",
        provider="assistx-router",
        return_format="verbatim",
        toolsets="terminal,file",
    )

    assert result["output"] == "PONG"
    # delegation toolset is appended even when not in the base list
    assert "delegation" in captured["toolsets"].split(",")
    # directive forces the opencode-cli provider + verbatim return contract
    assert 'provider="opencode-cli"' in captured["prompt"]
    assert 'return_format="verbatim"' in captured["prompt"]
    assert "TASK:" in captured["prompt"]


def test_run_hermes_delegated_default_return_format_is_verbatim(monkeypatch):
    captured = {}

    def fake_run_hermes(prompt, timeout=None, model=None, provider=None, toolsets=None):
        captured["prompt"] = prompt
        return {"success": True, "output": "x", "session_id": "s", "elapsed": 0.1}

    monkeypatch.setattr(adapter, "run_hermes", fake_run_hermes)
    adapter.run_hermes_delegated("do thing", toolsets="terminal")
    assert 'return_format="verbatim"' in captured["prompt"]


def test_process_task_routes_configured_tier_to_delegated(reload_with_delegate_tier, monkeypatch):
    """A tool-small task is solved via run_hermes_delegated, not run_hermes."""
    ad = reload_with_delegate_tier
    calls = {}

    def fake_delegated(prompt, timeout=None, model=None, provider=None, return_format=None):
        calls["delegated"] = True
        calls["return_format"] = return_format
        return {"success": True, "output": "PONG", "session_id": "s", "elapsed": 1.0}

    monkeypatch.setattr(ad, "run_hermes_delegated", fake_delegated)
    monkeypatch.setattr(ad, "run_hermes", lambda *a, **k: calls.setdefault("raw", True))

    assistx = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    assistx.claim_task.return_value = True
    assistx.get_context.return_value = {}
    # task text triggers the tool-small tier (typo/fix keywords)
    task = {"id": "t1", "title": "fix a typo in the readme", "description": "small edit"}

    ad.process_task(assistx, task)

    assert calls.get("delegated") is True
    assert "raw" not in calls  # run_hermes must NOT be used for this tier
    assert calls["return_format"] == "verbatim"
    # task was completed with the delegated (machine-usable) result
    assistx.complete_task.assert_called_once()
    _, kwargs = assistx.complete_task.call_args
    assert kwargs["status"] == "DONE"
    assert "PONG" in kwargs["result"]["output"]
