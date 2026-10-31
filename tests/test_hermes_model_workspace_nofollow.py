"""Hermetic Hermes model-workspace confinement regression tests.

All filesystem operations are restricted to pytest tmp_path. No production
knowledge root, model registry, service credentials or inference is accessed.
"""
from __future__ import annotations

import json
import os
import stat
import time

import pytest

from assistx.agents import hermes_agent_adapter as adapter


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    root = tmp_path / "knowledge"
    root.mkdir(mode=0o700)
    state = {"models": {}}
    writes = []
    monkeypatch.setenv("HERMES_KNOWLEDGE_ROOT", str(root))
    monkeypatch.setattr(adapter, "load_eval", lambda: state)
    monkeypatch.setattr(adapter, "save_eval", lambda data: writes.append(data))
    return root, state, writes


@pytest.mark.parametrize("model", [
    "", " ", "\t", ".", "..", "../outside", "./model", "model/..",
    "model/.", "a//b", "/absolute", "a/", "a\\b", "a%2Fb",
    "a%5cb", "model\nother", "model\x00x", "a\u2215b", "a\u2044b",
    "a\uff0fb", "a\u202eb", "assistx-model-v2-" + "a" * 64,
    "a" * 201, None, 3, ["model"],
])
def test_model_ids_that_cannot_be_proven_safe_are_refused(model):
    with pytest.raises(adapter.UnsafeModelWorkspace):
        adapter._safe_model_dir(model)


def test_flat_ids_preserve_legacy_name_and_namespaced_ids_are_unambiguous():
    assert adapter._safe_model_dir("provider_model") == "provider_model"
    assert adapter._safe_model_dir("plain-model:Q4_K_M") == "plain-model:Q4_K_M"
    namespaced = adapter._safe_model_dir("provider/model")
    assert namespaced != "provider_model"
    assert namespaced.startswith("assistx-model-v2-")
    assert len(namespaced) < 100
    assert namespaced == adapter._safe_model_dir("provider/model")
    assert namespaced != adapter._safe_model_dir("provider/model-v2")


def test_valid_flat_and_namespaced_workspaces_use_strict_children(workspace):
    root, state, writes = workspace
    for model in ("plain-model", "LiquidAI/LFM3-1.7B"):
        ws = adapter.ensure_model_env(model)
        assert ws == str(root / adapter._safe_model_dir(model))
        assert os.path.commonpath((str(root), ws)) == str(root)
        assert ws != str(root)
        assert os.path.isdir(ws)
        assert (root / adapter._safe_model_dir(model) / "ENV.md").is_file()
        assert stat.S_IMODE(os.stat(ws).st_mode) & 0o022 == 0
        assert stat.S_IMODE(os.stat(os.path.join(ws, "ENV.md")).st_mode) & 0o022 == 0
        assert state["models"][model]["workspace"] == ws
        assert state["models"][model]["environment_configured"] is True
    assert len(writes) == 2
    # Once configured, reopening does not overwrite the existing note.
    note = root / "plain-model" / "ENV.md"
    before = note.read_bytes()
    assert adapter.ensure_model_env("plain-model") == str(root / "plain-model")
    assert note.read_bytes() == before
    assert len(writes) == 2


@pytest.mark.parametrize("model", [
    "..", ".", "../secret", "model/../escape", "model\\x", "x%2fy", "x//y",
])
def test_invalid_model_makes_no_root_registry_or_workspace_io(workspace, monkeypatch, model):
    root, _, writes = workspace
    monkeypatch.setattr(
        adapter, "load_eval",
        lambda: pytest.fail("invalid model must not read model registry"),
    )
    assert adapter.ensure_model_env(model) is None
    assert list(root.iterdir()) == []
    assert writes == []


def test_legacy_flattened_namespaced_workspace_requires_review(workspace):
    root, state, writes = workspace
    legacy = root / "provider_model"
    legacy.mkdir()
    (legacy / "operator-note.txt").write_text("preserve")
    name = adapter._safe_model_dir("provider/model")
    assert adapter.ensure_model_env("provider/model") is None
    assert (legacy / "operator-note.txt").read_text() == "preserve"
    assert not (root / name).exists()
    assert state["models"] == {}
    assert writes == []


def test_symlinked_workspace_child_refused_without_following(workspace, tmp_path):
    root, _, writes = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_note = outside / "ENV.md"
    outside_note.write_text("unchanged")
    (root / "plain-model").symlink_to(outside, target_is_directory=True)
    assert adapter.ensure_model_env("plain-model") is None
    assert outside_note.read_text() == "unchanged"
    assert writes == []


def test_symlinked_namespaced_child_refused_without_following(workspace, tmp_path):
    root, _, writes = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / adapter._safe_model_dir("provider/model")).symlink_to(
        outside, target_is_directory=True,
    )
    assert adapter.ensure_model_env("provider/model") is None
    assert list(outside.iterdir()) == []
    assert writes == []


def test_symlinked_knowledge_root_refused(workspace, monkeypatch, tmp_path):
    root, _, writes = workspace
    alias = tmp_path / "knowledge-link"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv("HERMES_KNOWLEDGE_ROOT", str(alias))
    assert adapter.ensure_model_env("plain-model") is None
    assert list(root.iterdir()) == []
    assert writes == []


def test_symlinked_root_ancestor_refused(workspace, monkeypatch, tmp_path):
    root, _, _ = workspace
    container = tmp_path / "alias-parent"
    container.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setenv("HERMES_KNOWLEDGE_ROOT", str(container / "knowledge"))
    assert adapter.ensure_model_env("plain-model") is None
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("badroot", [
    "/not-existing/knowledge",
    "/tmp/../outside",
    "/tmp/./knowledge",
    "/tmp//knowledge",
    "/",
])
def test_missing_or_noncanonical_root_never_falls_back(workspace, monkeypatch, badroot):
    root, _, writes = workspace
    monkeypatch.setenv("HERMES_KNOWLEDGE_ROOT", badroot)
    assert adapter.ensure_model_env("plain-model") is None
    assert list(root.iterdir()) == []
    assert writes == []


def test_world_writable_knowledge_root_refused(workspace):
    root, _, writes = workspace
    before = stat.S_IMODE(root.stat().st_mode)
    try:
        root.chmod(0o777)
        assert adapter.ensure_model_env("plain-model") is None
    finally:
        root.chmod(before)
    assert list(root.iterdir()) == []
    assert writes == []


def test_existing_group_writable_child_refused(workspace):
    root, _, writes = workspace
    child = root / "plain-model"
    child.mkdir(mode=0o700)
    before = stat.S_IMODE(child.stat().st_mode)
    try:
        child.chmod(0o770)
        assert adapter.ensure_model_env("plain-model") is None
    finally:
        child.chmod(before)
    assert list(child.iterdir()) == []
    assert writes == []


def test_existing_env_note_is_not_overwritten(workspace):
    root, state, writes = workspace
    child = root / "plain-model"
    child.mkdir(mode=0o700)
    (child / "ENV.md").write_text("operator-owned")
    assert adapter.ensure_model_env("plain-model") is None
    assert (child / "ENV.md").read_text() == "operator-owned"
    assert writes == []
    assert state["models"]["plain-model"]["environment_configured"] is False


def test_symlinked_env_note_is_not_followed(workspace, tmp_path):
    root, _, writes = workspace
    child = root / "plain-model"
    child.mkdir(mode=0o700)
    target = tmp_path / "protected-note"
    target.write_text("never overwrite")
    (child / "ENV.md").symlink_to(target)
    assert adapter.ensure_model_env("plain-model") is None
    assert target.read_text() == "never overwrite"
    assert writes == []


def test_hardlinked_env_note_is_not_overwritten(workspace, tmp_path):
    root, _, writes = workspace
    child = root / "plain-model"
    child.mkdir(mode=0o700)
    target = tmp_path / "protected-note"
    target.write_text("never overwrite")
    os.link(target, child / "ENV.md")
    assert adapter.ensure_model_env("plain-model") is None
    assert target.read_text() == "never overwrite"
    assert writes == []


def test_unknown_workspace_failure_does_not_return_parent(workspace, monkeypatch):
    root, _, writes = workspace
    monkeypatch.setenv("HERMES_KNOWLEDGE_ROOT", str(root / "missing"))
    result = adapter.ensure_model_env("plain-model")
    assert result is None
    assert result != str(root)
    assert writes == []


@pytest.mark.parametrize("model", ["..", "bad//model", "unsafe%2Fmodel"])
def test_denied_workspace_blocks_worktree_and_provider_before_side_effects(
    workspace, monkeypatch, model,
):
    from unittest.mock import MagicMock

    root, _, _ = workspace
    client = MagicMock()
    client.claim_task.side_effect = lambda _task_id, sid: {
        "claimed": True,
        "task": {
            **polled,
            "payload_json": json.dumps(polled["payload"]),
            "status": "CLAIMED",
            "claim_id": "synthetic-claim",
            "claimed_by": adapter.AGENT_ID,
            "agent_session_id": sid,
            "lease_expires_at_ts": int(time.time() * 1000) + 120_000,
            "execution_attempt": 1,
        },
    }
    client.get_context.return_value = {}
    monkeypatch.setattr(
        adapter, "build_work_packet",
        lambda *_a, **_kw: {"synthetic": True},
    )
    monkeypatch.setattr(
        adapter, "prepare_repository",
        lambda *_a, **_kw: pytest.fail("repository side effect admitted"),
    )
    monkeypatch.setattr(
        adapter, "run_hermes",
        lambda *_a, **_kw: pytest.fail("model dispatch admitted"),
    )
    monkeypatch.setattr(
        adapter, "run_hermes_delegated",
        lambda *_a, **_kw: pytest.fail("delegated model dispatch admitted"),
    )

    polled = {
        "id": "synthetic-1",
        "title": "synthetic workspace refusal",
        "payload": {
            "model": model,
            "execution_contract": {"repository": "synthetic"},
        },
    }
    with pytest.raises(
        adapter.UnsafeModelWorkspace, match="model_workspace_unavailable",
    ):
        adapter.process_task(client, polled)
    client.heartbeat.assert_not_called()
    client.complete_task.assert_not_called()
    assert list(root.iterdir()) == []


def test_unavailable_root_blocks_valid_model_before_worktree(
    workspace, monkeypatch,
):
    from unittest.mock import MagicMock

    root, _, _ = workspace
    monkeypatch.setenv("HERMES_KNOWLEDGE_ROOT", str(root / "missing"))
    monkeypatch.setattr(
        adapter, "build_work_packet",
        lambda *_a, **_kw: {"synthetic": True},
    )
    monkeypatch.setattr(
        adapter, "prepare_repository",
        lambda *_a, **_kw: pytest.fail("repository side effect admitted"),
    )
    monkeypatch.setattr(
        adapter, "run_hermes",
        lambda *_a, **_kw: pytest.fail("model dispatch admitted"),
    )
    client = MagicMock()
    client.claim_task.side_effect = lambda _task_id, sid: {
        "claimed": True,
        "task": {
            **task,
            "payload_json": json.dumps(task["payload"]),
            "status": "CLAIMED",
            "claim_id": "synthetic-claim",
            "claimed_by": adapter.AGENT_ID,
            "agent_session_id": sid,
            "lease_expires_at_ts": int(time.time() * 1000) + 120_000,
            "execution_attempt": 1,
        },
    }
    client.get_context.return_value = {}
    task = {
        "id": "synthetic-1",
        "payload": {
            "model": "safe-model",
            "execution_contract": {"repository": "synthetic"},
        },
    }
    with pytest.raises(adapter.UnsafeModelWorkspace, match="model_workspace_unavailable"):
        adapter.process_task(client, task)
    client.complete_task.assert_not_called()
    assert list(root.iterdir()) == []
