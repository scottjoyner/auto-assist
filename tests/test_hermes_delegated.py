"""Tests for the opencode-cli delegation wiring in the Hermes agent adapter.

These verify that a task routed to a configured tier is solved via Hermes's
``delegate_task(provider="opencode-cli", return_format=...)`` return-contract
path (machine-usable results) rather than a free-form ``hermes chat`` session.
"""
import importlib
import hashlib
import json
import os
import time

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


def _synthetic_claim(task, session_id, claim_id, agent_id):
    """Return a synthetic source-owned claim, including the payload of this task."""
    return {
        "claimed": True,
        "task": {
            **task,
            "status": "CLAIMED",
            "claim_id": claim_id,
            "claimed_by": agent_id,
            "agent_session_id": session_id,
            "lease_expires_at_ts": int(time.time() * 1000) + 120_000,
            "execution_attempt": 1,
            "payload_json": json.dumps(task.get("payload", {})),
        },
    }


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
    assistx.claim_task.side_effect = lambda _id, sid: _synthetic_claim(
        task, sid, "claim-t1", ad.AGENT_ID,
    )
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
    assistx.claim_task.side_effect = lambda _id, sid: _synthetic_claim(
        task, sid, "claim-bounded", ad.AGENT_ID,
    )
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
    assistx.claim_task.side_effect = lambda _id, sid: _synthetic_claim(
        task, sid, "claim-fail", ad.AGENT_ID,
    )
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


def _authoritative_fixture(task_id="task-1", session_id="session-1", **overrides):
    source = {
        "id": task_id,
        "status": "CLAIMED",
        "claim_id": "claim-current",
        "claimed_by": adapter.AGENT_ID,
        "agent_session_id": session_id,
        "lease_expires_at_ts": int(time.time() * 1000) + 120_000,
        "execution_attempt": 1,
        "title": "claimed title",
        "payload_json": '{"model":"claimed/model"}',
    }
    source.update(overrides)
    return {"claimed": True, "task": source}


@pytest.mark.parametrize("changed, expected", [
    ({"task": None}, "CLAIM_TASK_ID_MISMATCH"),
    ({"task": {"id": "other"}}, "CLAIM_TASK_ID_MISMATCH"),
    ({"task": {"id": "task-1", "status": "READY"}}, "CLAIM_STATUS_INVALID"),
    ({"task": {"id": "task-1", "status": "CLAIMED", "claim_id": None}},
     "CLAIM_ID_MISSING"),
])
def test_incomplete_claim_snapshot_is_denied_before_use(changed, expected):
    source = _authoritative_fixture()
    if isinstance(changed["task"], dict):
        source["task"].update(changed["task"])
    else:
        source["task"] = changed["task"]
    with pytest.raises(adapter.ClaimSnapshotError, match=expected):
        adapter._authoritative_claimed_task(
            source, task_id="task-1", session_id="session-1",
        )


@pytest.mark.parametrize("overrides, expected", [
    ({"id": "different"}, "CLAIM_TASK_ID_MISMATCH"),
    ({"claimed_by": "untrusted-remote"}, "CLAIM_AGENT_MISMATCH"),
    ({"agent_session_id": "previous-session"}, "CLAIM_SESSION_MISMATCH"),
    ({"status": "CANCELLED"}, "CLAIM_STATUS_INVALID"),
    ({"claim_id": ""}, "CLAIM_ID_MISSING"),
    ({"lease_expires_at_ts": 0}, "CLAIM_LEASE_EXPIRED"),
    ({"lease_expires_at_ts": True}, "CLAIM_LEASE_EXPIRED"),
    ({"execution_attempt": 0}, "CLAIM_ATTEMPT_INVALID"),
    ({"payload_json": '{"model":'}, "CLAIMED_PAYLOAD_INVALID"),
    ({"payload_json": '["not an object"]'}, "CLAIMED_PAYLOAD_INVALID"),
    ({"payload_json": '{"model":"A","model":"B"}'}, "CLAIMED_PAYLOAD_INVALID"),
    ({"payload_json": '{"model":"A","x":NaN}'}, "CLAIMED_PAYLOAD_INVALID"),
    ({"payload_json": None, "payload": {"model": "stale"}},
     "CLAIMED_PAYLOAD_INVALID"),
    ({"execution_attempt": True}, "CLAIM_ATTEMPT_INVALID"),
])
def test_claimed_snapshot_rejects_invalid_source_and_payload(overrides, expected):
    source = _authoritative_fixture(**overrides)
    with pytest.raises(adapter.ClaimSnapshotError, match=expected):
        adapter._authoritative_claimed_task(
            source, task_id="task-1", session_id="session-1",
        )


def test_claim_payload_absent_does_not_reuse_polled_task():
    claim = _authoritative_fixture()
    claim["task"].pop("payload_json")
    with pytest.raises(adapter.ClaimSnapshotError, match="CLAIMED_PAYLOAD_MISSING"):
        adapter._authoritative_claimed_task(
            claim, task_id="task-1", session_id="session-1",
        )


def test_claim_response_must_not_disagree_about_claim_identity():
    claim = _authoritative_fixture()
    claim["claim_id"] = "different"
    with pytest.raises(adapter.ClaimSnapshotError, match="CLAIM_ID_MISMATCH"):
        adapter._authoritative_claimed_task(
            claim, task_id="task-1", session_id="session-1",
        )


def test_claimed_payload_is_frozen_and_consistent_across_legacy_accessors():
    claim = _authoritative_fixture(
        payload={"model": "stale/model"},
        payload_json='{"model":"claimed/model","prompt":"claim only"}',
    )
    result, parsed, claim_id = adapter._authoritative_claimed_task(
        claim, task_id="task-1", session_id="session-1",
    )
    assert claim_id == "claim-current"
    assert parsed["model"] == "claimed/model"
    assert result["payload"]["model"] == "claimed/model"
    assert json.loads(result["payload_json"]) == result["payload"]
    parsed["model"] = "modified"
    assert claim["task"]["payload_json"] != result["payload_json"]


def test_process_task_uses_claimed_snapshot_for_prompt_and_model(monkeypatch):
    from unittest.mock import MagicMock

    polled = {
        "id": "task-1",
        "title": "STALE POLL TITLE",
        "description": "STALE POLL DESCRIPTION",
        "payload": {
            "model": "stale/model",
            "execution_contract": {"repository": "stale-repo"},
        },
    }
    client = MagicMock()
    client.claim_task.side_effect = lambda _id, sid: _authoritative_fixture(
        session_id=sid,
        title="CLAIMED TASK TITLE",
        description="CLAIMED DESCRIPTION",
        payload_json='{"model":"claimed/model","prompt":"CLAIMED PROMPT"}',
    )
    client.get_context.return_value = {}
    seen = {}
    monkeypatch.setattr(adapter, "get_model_prompt", lambda _: "")
    monkeypatch.setattr(adapter, "classify_model_tier", lambda *_args: "reasoning-mid")
    monkeypatch.setattr(adapter, "ensure_model_env", lambda model: seen.setdefault("model", model))
    monkeypatch.setattr(adapter, "fleet", None)
    monkeypatch.setattr(adapter, "record_task_eval", lambda *_args, **_kwargs: None)

    def fake_run(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["inference_model"] = kwargs["model"]
        return {"success": True, "output": "Completed the claimed task with detailed output.",
                "elapsed": 0.01, "session_id": None}

    monkeypatch.setattr(adapter, "run_hermes", fake_run)
    monkeypatch.setattr(
        adapter, "prepare_repository",
        lambda *_args, **_kw: pytest.fail("stale work packet prepared"),
    )
    adapter.process_task(client, polled)
    assert "STALE POLL TITLE" not in seen["prompt"]
    assert "STALE POLL DESCRIPTION" not in seen["prompt"]
    assert "CLAIMED TASK TITLE" in seen["prompt"]
    assert "CLAIMED DESCRIPTION" in seen["prompt"]
    assert seen["model"] == seen["inference_model"] == "claimed/model"
    assert polled["payload"]["model"] == "stale/model"
    client.get_context.assert_called_once_with("task-1", "CLAIMED TASK TITLE")


def test_repository_preparation_receives_only_claimed_execution_contract(monkeypatch):
    from unittest.mock import MagicMock

    polled = {
        "id": "task-1", "title": "stale",
        "payload": {"model": "stale/model",
                    "execution_contract": {"repository": "stale-repo"}},
    }
    client = MagicMock()
    client.claim_task.side_effect = lambda _id, sid: _authoritative_fixture(
        session_id=sid,
        title="trusted",
        payload_json=json.dumps({
            "model": "claimed/model",
            "execution_contract": {"repository": "claimed-repo",
                                   "recommended_tier": "reasoning-mid"},
        }),
    )
    client.get_context.return_value = {}
    seen = {}
    def fake_packet(task, learned_lessons):
        seen["packet_task"] = task
        return {"synthetic": True}

    def fake_prepare(contract, **kwargs):
        seen["contract"] = contract
        seen["preparation"] = kwargs
        return {"ok": False, "reason": "synthetic_denied"}

    monkeypatch.setattr(adapter, "build_work_packet", fake_packet)
    monkeypatch.setattr(adapter, "prepare_repository", fake_prepare)
    monkeypatch.setattr(adapter, "get_model_prompt", lambda _: "")
    monkeypatch.setattr(adapter, "ensure_model_env", lambda *_: None)
    monkeypatch.setattr(adapter, "fleet", None)
    monkeypatch.setattr(adapter, "record_task_eval", lambda *_a, **_kw: None)
    monkeypatch.setattr(adapter, "collect_executor_evidence", lambda *_a, **_kw: {})
    monkeypatch.setattr(adapter, "cleanup_worktree", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        adapter, "evaluate_completion",
        lambda *_a, **_kw: {"accepted": False, "reasons": ["synthetic"]},
    )
    monkeypatch.setattr(
        adapter, "run_hermes",
        lambda *_a, **_kw: pytest.fail("no provider execution expected"),
    )
    adapter.process_task(client, polled)
    assert seen["contract"]["repository"] == "claimed-repo"
    assert seen["packet_task"]["payload"]["execution_contract"] == seen["contract"]
    assert seen["packet_task"]["title"] == "trusted"
    assert seen["preparation"]["execution_attempt"] == 1


def test_invalid_claim_does_not_reach_context_repository_or_model(monkeypatch):
    from unittest.mock import MagicMock

    client = MagicMock()
    client.claim_task.side_effect = lambda _id, sid: _authoritative_fixture(
        session_id=sid, payload_json="{not JSON",
    )
    monkeypatch.setattr(
        adapter, "ensure_model_env",
        lambda *_args: pytest.fail("invalid claim opened a model workspace"),
    )
    monkeypatch.setattr(
        adapter, "prepare_repository",
        lambda *_args, **_kw: pytest.fail("invalid claim prepared repository"),
    )
    monkeypatch.setattr(
        adapter, "run_hermes",
        lambda *_args, **_kw: pytest.fail("invalid claim started inference"),
    )
    with pytest.raises(adapter.ClaimSnapshotError, match="CLAIMED_PAYLOAD_INVALID"):
        adapter.process_task(
            client,
            {"id": "task-1", "payload": {"model": "stale/model"}},
        )
    client.get_context.assert_not_called()
    client.heartbeat.assert_not_called()
    client.complete_task.assert_not_called()
