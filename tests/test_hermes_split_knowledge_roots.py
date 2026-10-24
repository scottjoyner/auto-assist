"""No-network, synthetic-only split-root acceptance and fail-closed tests."""
from __future__ import annotations
import importlib
import json
import os
from pathlib import Path

import pytest

from assistx import knowledge_roots as roots
from assistx import swarm_memory as memory
from assistx.agents import hermes_agent_adapter as adapter


@pytest.fixture
def split(tmp_path, monkeypatch):
    corpus = tmp_path / "source"
    scratch = tmp_path / "scratch"
    corpus.mkdir()
    scratch.mkdir()
    (corpus / "decision.md").write_text("SYNTHETIC_DECISION_ONLY\n")
    (corpus / "notes").mkdir()
    (corpus / "notes" / "context.txt").write_text("SYNTHETIC_CONTEXT_ONLY\n")
    with monkeypatch.context() as mp:
        mp.setenv(roots.READ_ENV, str(corpus))
        mp.setenv(roots.WRITE_ENV, str(scratch))
        mp.setenv("HERMES_KNOWLEDGE_ROOT", str(scratch))
        mp.setenv("HERMES_EVAL_PATH", str(scratch / "eval.json"))
        mp.setenv("HERMES_SELFTASK_BULK_MODELS", "synthetic/model")
        importlib.reload(adapter)
        importlib.reload(memory)
        yield adapter, memory, corpus, scratch, mp
    importlib.reload(adapter)
    importlib.reload(memory)


def test_split_roots_no_legacy_fallback(split):
    ad, mem, ro, rw, _ = split
    assert ad.KNOWLEDGE_SPLIT_ENABLED is True
    assert ad.KNOWLEDGE_READ_ROOT == str(ro)
    assert ad.KNOWLEDGE_WRITE_ROOT == str(rw)
    assert mem.KNOWLEDGE_READ_ROOT == str(ro)
    assert mem.MEMORY_INDEX_PATH == str(rw / ".memory" / "index.json")


def test_actual_bounded_snapshot_uses_readonly_source(split):
    ad, _, ro, rw, _ = split
    snapshot = ad._gather_knowledge_context(max_chars=2000)
    assert "SYNTHETIC_DECISION_ONLY" in snapshot
    assert "SYNTHETIC_CONTEXT_ONLY" in snapshot
    assert str(rw) not in snapshot


def test_actual_selftask_writes_only_to_scratch(split):
    ad, _, ro, rw, mp = split
    mp.setattr(ad, "fleet", None)
    mp.setattr(ad, "swarm_memory", None)
    mp.setattr(ad, "call_self_task_llm", lambda *a, **k: {
        "success": True, "output": "Synthetic structured analysis, non-trivial.",
        "elapsed": 0.01,
    })
    ad.process_self_task(None, "bulk_summarize")
    assert (rw / "SUMMARY.md").read_text().startswith("Synthetic structured")
    assert not (ro / "SUMMARY.md").exists()
    registry = json.loads((rw / "eval.json").read_text())
    assert registry["models"]["synthetic/model"]["tasks"]["self:bulk_summarize"]["success"] == 1
    assert ad._self_artifact_present("synthetic/model", since_ts=0)[0] is False
    assert ad._self_artifact_present("synthetic/model", since_ts=__import__("time").time())[0] is True


def test_actual_model_env_and_evaluation_write_only_to_scratch(split):
    ad, _, ro, rw, _ = split
    model_ws = ad.ensure_model_env("synthetic/model")
    assert model_ws == str(rw / "synthetic_model")
    assert (rw / "synthetic_model" / "ENV.md").exists()
    assert not (ro / "synthetic_model").exists()
    ad.record_task_eval("synthetic/model", "snapshot", True, 1.0)
    assert ad.load_eval()["models"]["synthetic/model"]["tasks"]["snapshot"]["runs"] == 1
    assert ad._gather_knowledge_context().count("SYNTHETIC_DECISION_ONLY") == 1


def test_restart_reload_preserves_eval_memory_and_output(split):
    ad, mem, ro, rw, mp = split
    ad.record_task_eval("synthetic/model", "snapshot", True, 1.0)
    (rw / "recap.md").write_text("SYNTHETIC_OUTPUT_ONLY\n")
    first = mem.MemoryIndex()
    first.items = [{"text": "synthetic-only", "vec": [1.0, 0.0],
                    "meta": {"source": "decision.md"}}]
    first.save()
    importlib.reload(ad)
    importlib.reload(mem)
    assert ad.load_eval()["models"]["synthetic/model"]["tasks"]["snapshot"]["runs"] == 1
    assert (rw / "recap.md").read_text() == "SYNTHETIC_OUTPUT_ONLY\n"
    assert mem.MemoryIndex().items[0]["text"] == "synthetic-only"
    assert (rw / ".memory" / "index.json").is_file()


def test_semantic_index_reads_ro_and_writes_rw(split):
    _, mem, ro, rw, mp = split
    mp.setattr(mem, "embed", lambda text: [1.0, float(len(text))])
    mp.setattr(mem.time, "sleep", lambda *_: None)
    idx = mem.MemoryIndex()
    count = idx.index_vault()
    assert count >= 2
    assert (rw / ".memory" / "index.json").exists()
    assert not (ro / ".memory").exists()
    sources = {item["meta"]["source"] for item in idx.items}
    assert {"decision.md", "notes/context.txt"}.issubset(sources)


def test_read_context_skips_symlinks_and_outside_files(split, tmp_path):
    ad, mem, ro, rw, _ = split
    outside = tmp_path / "other.md"
    outside.write_text("FORBIDDEN_SECRET_STRING")
    (ro / "evil.md").symlink_to(outside)
    (ro / "linked_dir").symlink_to(outside.parent, target_is_directory=True)
    assert "FORBIDDEN_SECRET_STRING" not in ad._gather_knowledge_context()
    assert all("evil.md" != Path(p).name for p in
               roots.safe_knowledge_files(str(ro), set()))
    with pytest.raises(OSError):
        roots.safe_read_text(str(ro / "evil.md"), 500)


def test_reject_one_sided_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv(roots.READ_ENV, str(tmp_path / "read"))
    monkeypatch.delenv(roots.WRITE_ENV, raising=False)
    with pytest.raises(ValueError, match="both"):
        roots.resolve_knowledge_roots("/unused")


@pytest.mark.parametrize("ro,rw", [
    ("/tmp/fixture", "/tmp/fixture"),
    ("/tmp/fixture", "/tmp/fixture/out"),
    ("/tmp/fixture/out", "/tmp/fixture"),
])
def test_overlap_rejected(monkeypatch, ro, rw):
    monkeypatch.setenv(roots.READ_ENV, ro)
    monkeypatch.setenv(roots.WRITE_ENV, rw)
    with pytest.raises(ValueError, match="overlap"):
        roots.resolve_knowledge_roots("/unused")


def test_disallow_symlinked_source_and_write_root(monkeypatch, tmp_path):
    ro = tmp_path / "ro"; rw = tmp_path / "rw"
    ro.mkdir(); rw.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(ro, target_is_directory=True)
    monkeypatch.setenv(roots.READ_ENV, str(alias))
    monkeypatch.setenv(roots.WRITE_ENV, str(rw))
    with pytest.raises(ValueError, match="symbolic"):
        roots.resolve_knowledge_roots("/unused")


def test_eval_must_be_inside_rw(monkeypatch, tmp_path):
    ro = tmp_path / "read"; rw = tmp_path / "write"
    ro.mkdir(); rw.mkdir()
    monkeypatch.setenv(roots.READ_ENV, str(ro))
    monkeypatch.setenv(roots.WRITE_ENV, str(rw))
    with pytest.raises(ValueError, match="eval"):
        roots.resolve_knowledge_roots("/legacy", str(ro / "eval.json"))


def test_write_target_alias_rejected(split, tmp_path):
    ad, _, ro, rw, mp = split
    outside = tmp_path / "outside"; outside.mkdir()
    (rw / "synthetic_model").symlink_to(outside, target_is_directory=True)
    assert ad.ensure_model_env("synthetic/model") is None
    assert not (outside / "ENV.md").exists()
    (rw / "SUMMARY.md").symlink_to(outside / "victim.md")
    mp.setattr(ad, "fleet", None)
    mp.setattr(ad, "swarm_memory", None)
    mp.setattr(ad, "call_self_task_llm", lambda *a, **k: {
        "success": True, "output": "Synthetic output which must be rejected",
        "elapsed": 0.1,
    })
    ad.process_self_task(None, "bulk_summarize")
    assert not (outside / "victim.md").exists()
    assert (rw / "SUMMARY.md").is_symlink()


def test_model_traversal_rejected():
    for value in ("..", "../outside", "/absolute", "foo/../bar", "a\\b", "a"*129):
        with pytest.raises(ValueError):
            roots.safe_model_directory(value, split=True)


def test_legacy_unsplit_defaults_are_preserved(monkeypatch, tmp_path):
    with monkeypatch.context() as mp:
        mp.delenv(roots.READ_ENV, raising=False)
        mp.delenv(roots.WRITE_ENV, raising=False)
        mp.setenv("HERMES_KNOWLEDGE_ROOT", str(tmp_path))
        mp.setenv("HERMES_EVAL_PATH", str(tmp_path / "model-profiles.json"))
        importlib.reload(adapter)
        assert adapter.KNOWLEDGE_SPLIT_ENABLED is False
        assert adapter.KNOWLEDGE_READ_ROOT == str(tmp_path)
        assert adapter.KNOWLEDGE_WRITE_ROOT == str(tmp_path)
        adapter.record_task_eval("synthetic/model", "snapshot", True, 1.0)
        assert (tmp_path / "model-profiles.json").is_file()
    importlib.reload(adapter)


def test_registry_symlink_alias_never_read_or_written(split, tmp_path):
    ad, _, _, rw, _ = split
    outside = tmp_path / "secret-outside.json"
    outside.write_text('{"models":{"SENSITIVE_EXTERNAL_STATE":{}}}')
    (rw / "eval.json").symlink_to(outside)
    assert ad.load_eval() == {"models": {}}
    ad.record_task_eval("synthetic/model", "snapshot", True, 1.0)
    assert outside.read_text() == '{"models":{"SENSITIVE_EXTERNAL_STATE":{}}}'
    assert (rw / "eval.json").is_symlink()


def test_semantic_index_symlink_alias_never_read_or_written(split, tmp_path):
    _, mem, _, rw, _ = split
    outside = tmp_path / "outside-index.json"
    outside.write_text('{"items":[{"text":"LEAKED_OUTSIDE"}]}')
    (rw / ".memory").mkdir()
    (rw / ".memory" / "index.json").symlink_to(outside)
    index = mem.MemoryIndex()
    assert index.items == []
    with pytest.raises(ValueError):
        index.save()
    assert "LEAKED_OUTSIDE" in outside.read_text()


def test_missing_knowledge_root_alias_and_restricted_paths_fail_closed(monkeypatch, tmp_path):
    read = tmp_path / "read"
    read.mkdir()
    write = tmp_path / "write"
    write.mkdir()
    monkeypatch.setenv(roots.READ_ENV, str(read))
    monkeypatch.setenv(roots.WRITE_ENV, str(write))
    for bad in ("relative/path", str(read / ".."), "", str(read) + "/"):
        if bad:
            with pytest.raises(ValueError):
                roots._strict_path(bad, "fixture")
    with pytest.raises(ValueError):
        roots.checked_write_path(str(write), str(read / "victim"))
