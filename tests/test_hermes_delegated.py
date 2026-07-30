"""Tests for the opencode-cli delegation wiring in the Hermes agent adapter.

These verify that a task routed to a configured tier is solved via Hermes's
``delegate_task(provider="opencode-cli", return_format=...)`` return-contract
path (machine-usable results) rather than a free-form ``hermes chat`` session.
"""
import importlib
import hashlib
import json
import os

import pytest

import assistx.agents.hermes_agent_adapter as adapter
from assistx.improvement_cycle import build_execution_contract
from assistx.improvement_runtime import sign_executor_evidence


def executor_evidence(**values):
    patch = "diff --git a/tests/test_small.py b/tests/test_small.py\n"
    evidence = {
        "evidence_source": "executor",
        "executor_id": "test-agent",
        "worktree_clean_before": True,
        "isolated_worktree": True,
        "scope_validated": True,
        "patch": patch,
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        **values,
    }
    return sign_executor_evidence(
        evidence,
        key_id="node-v1",
        secret="node-secret",
    )


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
    monkeypatch.setenv("ASSISTX_IMPROVEMENT_ATTESTATION_KEY_ID", "node-v1")
    monkeypatch.setenv("ASSISTX_IMPROVEMENT_ATTESTATION_SECRET", "node-secret")
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


def test_hermes_child_cannot_read_executor_signing_secrets(monkeypatch):
    captured = {}
    monkeypatch.setenv("ASSISTX_IMPROVEMENT_ATTESTATION_SECRET", "signing-secret")
    monkeypatch.setenv(
        "ASSISTX_IMPROVEMENT_VERIFY_KEYS",
        '{"node-v1":"signing-secret"}',
    )
    monkeypatch.setenv("ASSISTX_REPOSITORY_ROOTS_JSON", '{"repo":"/private"}')

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(*_args, **kwargs):
        captured.update(kwargs["env"])
        return Result()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    adapter.run_hermes("bounded task", model="model")

    assert "ASSISTX_IMPROVEMENT_ATTESTATION_SECRET" not in captured
    assert "ASSISTX_IMPROVEMENT_VERIFY_KEYS" not in captured
    assert "ASSISTX_REPOSITORY_ROOTS_JSON" not in captured


def test_process_task_routes_configured_tier_to_delegated(reload_with_delegate_tier, monkeypatch):
    """A tool-small task is solved via run_hermes_delegated, not run_hermes."""
    ad = reload_with_delegate_tier
    calls = {}
    monkeypatch.setattr(ad, "ensure_model_env", lambda _model: "/tmp")
    monkeypatch.setattr(
        ad,
        "select_tier_model",
        lambda *_args, **_kwargs: "refinedtoolcallv5-3b",
    )

    def fake_delegated(
        prompt,
        timeout=None,
        model=None,
        provider=None,
        return_format=None,
        toolsets=None,
    ):
        calls["delegated"] = True
        calls["return_format"] = return_format
        calls["toolsets"] = toolsets
        return {"success": True, "output": "PONG", "session_id": "s", "elapsed": 1.0}

    monkeypatch.setattr(ad, "run_hermes_delegated", fake_delegated)
    monkeypatch.setattr(
        ad,
        "prepare_repository",
        lambda _contract, **_kwargs: {
            "ok": True,
            "root": "/tmp/repo",
            "head": "abc",
            "clean_before": True,
        },
    )
    monkeypatch.setattr(
        ad,
        "collect_executor_evidence",
        lambda _contract, _prepared, reported, **_kwargs: executor_evidence(
            **reported
        ),
    )
    monkeypatch.setattr(ad, "run_hermes", lambda *a, **k: calls.setdefault("raw", True))

    assistx = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    assistx.claim_task.return_value = {
        "claimed": True,
        "task": {"id": "t1", "claim_id": "claim-t1"},
    }
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
    assert kwargs["claim_id"] == "claim-t1"


def test_bounded_change_uses_restricted_tools_and_structured_evidence(
    reload_with_delegate_tier,
    monkeypatch,
):
    ad = reload_with_delegate_tier
    monkeypatch.setattr(ad, "ensure_model_env", lambda _model: "/tmp")
    monkeypatch.setattr(
        ad,
        "select_tier_model",
        lambda *_args, **_kwargs: "refinedtoolcallv5-3b",
    )
    envelope = {
        "changed_files": ["tests/test_small.py"],
        "diff_lines": 12,
        "tools_used": [
            "inspect_file",
            "apply_patch",
            "run_verification",
            "inspect_diff",
        ],
        "verification": [
            {
                "command": ["pytest", "-q", "tests/test_small.py"],
                "returncode": 0,
            }
        ],
        "summary": "Added the focused test.",
    }
    captured = {}

    def fake_delegated(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return {
            "success": True,
            "output": json.dumps(envelope),
            "session_id": "session",
            "elapsed": 1.0,
        }

    monkeypatch.setattr(ad, "run_hermes_delegated", fake_delegated)
    monkeypatch.setattr(
        ad,
        "prepare_repository",
        lambda _contract, **_kwargs: {
            "ok": True,
            "root": "/tmp/repo",
            "base_root": "/tmp/base",
            "head": "abc",
            "clean_before": True,
            "isolated": True,
        },
    )
    monkeypatch.setattr(
        ad,
        "collect_executor_evidence",
        lambda _contract, _prepared, reported, **_kwargs: executor_evidence(
            **reported
        ),
    )
    monkeypatch.setattr(ad, "cleanup_worktree", lambda _prepared: {"cleaned": True})
    assistx = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    assistx.claim_task.return_value = {
        "claimed": True,
        "task": {"id": "bounded-1", "claim_id": "claim-bounded"},
    }
    assistx.get_context.return_value = {}
    task = {
        "id": "bounded-1",
        "title": "Add a bounded test",
        "kind": "bounded_code_change",
        "payload": {
            "execution_contract": build_execution_contract(
                repository="auto-assist",
                objective="Add one focused test",
                allowed_paths=["tests/test_small.py"],
                verification_commands=[
                    ["pytest", "-q", "tests/test_small.py"]
                ],
            )
        },
    }

    ad.process_task(assistx, task)

    assert captured["toolsets"] == "terminal,file,code_execution"
    assert captured["cwd"] == "/tmp/repo"
    assert "Follow this work packet literally" in captured["prompt"]
    _, kwargs = assistx.complete_task.call_args
    assert kwargs["claim_id"] == "claim-bounded"
    attested = kwargs["result"]["completion_envelope"]
    assert all(attested[key] == value for key, value in envelope.items())
    assert attested["evidence_source"] == "executor"
    assert attested["attestation"]["key_id"] == "node-v1"


def test_bounded_task_reports_failed_when_executor_rejects_evidence(
    reload_with_delegate_tier,
    monkeypatch,
):
    ad = reload_with_delegate_tier
    monkeypatch.setattr(ad, "select_tier_model", lambda *_args, **_kwargs: "model")
    monkeypatch.setattr(ad, "ensure_model_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ad, "fleet", None)
    monkeypatch.setattr(ad, "record_task_eval", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ad,
        "run_hermes",
        lambda *_args, **_kwargs: {
            "success": True,
            "output": '{"changed_files":["src/outside.py"]}',
            "elapsed": 1.0,
            "session_id": None,
        },
    )
    monkeypatch.setattr(
        ad,
        "prepare_repository",
        lambda _contract, **_kwargs: {
            "ok": True,
            "root": "/tmp/repo",
            "base_root": "/tmp/base",
            "head": "abc",
            "clean_before": True,
            "isolated": True,
        },
    )
    monkeypatch.setattr(
        ad,
        "collect_executor_evidence",
        lambda *_args, **_kwargs: executor_evidence(
            scope_validated=False,
            changed_files=["src/outside.py"],
            diff_lines=4,
            tools_used=["inspect_file", "apply_patch", "inspect_diff"],
            verification=[],
        ),
    )
    monkeypatch.setattr(ad, "cleanup_worktree", lambda _prepared: {"cleaned": True})
    assistx = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    assistx.claim_task.return_value = {
        "claimed": True,
        "task": {"id": "bounded-fail", "claim_id": "claim-fail"},
    }
    assistx.get_context.return_value = {}
    task = {
        "id": "bounded-fail",
        "title": "Bounded failure",
        "kind": "bounded_code_change",
        "payload": {
            "execution_contract": build_execution_contract(
                repository="auto-assist",
                objective="Edit the allowed test",
                allowed_paths=["tests/test_small.py"],
                verification_commands=[["pytest", "-q", "tests/test_small.py"]],
                recommended_tier="reasoning-large",
            )
        },
    }

    ad.process_task(assistx, task)

    _, kwargs = assistx.complete_task.call_args
    assert kwargs["status"] == "FAILED"
    assert "scope_not_executor_validated" in kwargs["result"]["error"]
    assistx.write_memory.assert_not_called()
