"""Synthetic same-process corpus revalidation; never accept stale model context."""
from __future__ import annotations
import hashlib
from unittest.mock import Mock

import pytest

from assistx import hermes_corpus_custody as c
from assistx import swarm_memory
from assistx.agents import hermes_agent_adapter as adapter

from test_hermes_signed_corpus_custody import signed


def consume(bundle):
    ro, rw, key, *_ = bundle
    return c.verified_context_texts(str(ro), str(rw), str(key))


def test_signed_context_consumes_exact_verified_bytes(signed):
    data = dict(consume(signed))
    assert data == {
        "decision.md": "synthetic decision only\n",
        "nested/context.txt": "synthetic nested only\n",
    }


@pytest.mark.parametrize("mutation", [
    "change", "remove", "add_unsigned", "nested_unsigned",
    "symlink", "wrong_manifest", "bad_public_key",
])
def test_post_start_mutations_fail_at_consumption(signed, tmp_path, mutation):
    assert len(consume(signed)) == 2  # prior accepted startup
    ro, rw, key, signer, payload, issue = signed
    if mutation == "change":
        (ro / "decision.md").write_text("MUTATED_AFTER_STARTUP")
    elif mutation == "remove":
        (ro / "decision.md").unlink()
    elif mutation == "add_unsigned":
        (ro / "unknown.md").write_text("post-start data")
    elif mutation == "nested_unsigned":
        (ro / "nested" / "additional.txt").write_text("new data")
    elif mutation == "symlink":
        (ro / "decision.md").unlink()
        (ro / "decision.md").symlink_to(tmp_path / "unapproved.txt")
    elif mutation == "wrong_manifest":
        (ro / c.MANIFEST_NAME).write_text("{}")
    else:
        key.write_bytes(b"!"*32)
    with pytest.raises((ValueError, OSError)):
        consume(signed)


def test_mutation_between_manifest_check_and_second_read_rejected(signed, monkeypatch):
    ro, rw, key, *_ = signed
    initial = consume(signed)
    real = c._safe_regular
    opened = []
    def raced(path, bound):
        if path.endswith("decision.md"):
            opened.append(path)
            # verification opens once, consumption opens it a second time.
            if len(opened) == 2:
                (ro / "decision.md").write_text("POST_VERIFICATION_RACE")
        return real(path, bound)
    with monkeypatch.context() as mp:
        mp.setattr(c, "_safe_regular", raced)
        with pytest.raises(ValueError, match="changed during consumption"):
            consume(signed)
    assert len(opened) == 2
    assert len(initial) == 2


def test_actual_adapter_rechecks_after_startup(signed, monkeypatch):
    ro, rw, key, *_ = signed
    with monkeypatch.context() as mp:
        mp.setenv("HERMES_REQUIRE_SIGNED_CORPUS", "true")
        mp.setenv("HERMES_CORPUS_TRUST_KEY_PATH", str(key))
        mp.setattr(adapter, "KNOWLEDGE_READ_ROOT", str(ro))
        mp.setattr(adapter, "KNOWLEDGE_WRITE_ROOT", str(rw))
        first = adapter._gather_knowledge_context()
        assert "synthetic decision only" in first
        (ro / "decision.md").write_text("UNSIGNED_MUTATION")
        with pytest.raises(ValueError):
            adapter._gather_knowledge_context()


def test_selftask_fails_before_any_model_call_on_post_start_mutation(signed, monkeypatch):
    ro, rw, key, *_ = signed
    with monkeypatch.context() as mp:
        mp.setenv("HERMES_REQUIRE_SIGNED_CORPUS", "true")
        mp.setenv("HERMES_CORPUS_TRUST_KEY_PATH", str(key))
        mp.setattr(adapter, "KNOWLEDGE_READ_ROOT", str(ro))
        mp.setattr(adapter, "KNOWLEDGE_WRITE_ROOT", str(rw))
        mp.setattr(adapter, "fleet", None)
        mp.setattr(adapter, "_pick_bulk_model", lambda *_a: "synthetic/model")
        service = Mock()
        service.vault_semantic_context.side_effect = AssertionError("stale index consulted")
        mp.setattr(adapter, "swarm_memory", service)
        called = Mock(side_effect=AssertionError("model dispatch MUST NOT run"))
        mp.setattr(adapter, "call_self_task_llm", called)
        assert "synthetic decision only" in adapter._gather_knowledge_context()
        (ro / "decision.md").write_text("tampered after startup")
        with pytest.raises(ValueError):
            adapter.process_self_task(None, archetype="bulk_summarize")
        called.assert_not_called()
        service.vault_semantic_context.assert_not_called()
        assert not (rw / "SUMMARY.md").exists()


def test_signed_mode_blocks_old_semantic_index_and_new_index_build(monkeypatch):
    with monkeypatch.context() as mp:
        mp.setenv("HERMES_REQUIRE_SIGNED_CORPUS", "true")
        with pytest.raises(ValueError, match="stale"):
            swarm_memory.vault_semantic_context("anything", fallback="old data")
        with pytest.raises(ValueError, match="unbound"):
            swarm_memory.MemoryIndex()


def test_signed_mode_accepts_resigned_new_content_but_not_as_rollback_proof(signed):
    ro, rw, key, signer, payload, issue = signed
    assert len(consume(signed)) == 2
    data = b"new intentionally signed content\n"
    (ro / "decision.md").write_bytes(data)
    payload["entries"][0]["size"] = len(data)
    payload["entries"][0]["sha256"] = hashlib.sha256(data).hexdigest()
    issue(payload)
    assert dict(consume(signed))["decision.md"] == data.decode()
    # Freshness and revocation MUST still be checked by an independent witness.
    assert not hasattr(c, "verify_independent_high_water")


def test_legacy_semantic_mode_unchanged(monkeypatch):
    with monkeypatch.context() as mp:
        mp.delenv("HERMES_REQUIRE_SIGNED_CORPUS", raising=False)
        mp.setattr(swarm_memory, "_embed_broken_until", float("inf"))
        assert swarm_memory.vault_semantic_context("test", fallback="legacy fallback") == "legacy fallback"


def test_failed_later_signed_entry_releases_no_partial_model_context(signed, monkeypatch):
    ro, rw, key, *_ = signed
    assert len(consume(signed)) == 2
    (ro / "nested" / "context.txt").write_text("TAMPERED_SECOND_FILE")
    with monkeypatch.context() as mp:
        mp.setenv("HERMES_REQUIRE_SIGNED_CORPUS", "true")
        mp.setenv("HERMES_CORPUS_TRUST_KEY_PATH", str(key))
        mp.setattr(adapter, "KNOWLEDGE_READ_ROOT", str(ro))
        mp.setattr(adapter, "KNOWLEDGE_WRITE_ROOT", str(rw))
        with pytest.raises(ValueError):
            adapter._gather_knowledge_context()


def test_signed_mode_refuses_persisted_index_even_if_previously_saved(signed, monkeypatch):
    ro, rw, key, *_ = signed
    previous = rw / "stale-index.json"
    previous.write_text('{"items":[{"text":"STALE_CONTEXT"}]}')
    with monkeypatch.context() as mp:
        mp.setenv("HERMES_REQUIRE_SIGNED_CORPUS", "true")
        with pytest.raises(ValueError, match="unbound"):
            swarm_memory.MemoryIndex(str(previous))
