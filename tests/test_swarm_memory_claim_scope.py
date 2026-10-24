"""Zero-network tests for fail-closed Hermes semantic-memory router identity."""
from __future__ import annotations

import base64
import json

import pytest

from assistx import swarm_memory as memory


def synthetic_token(models):
    """Unsigned shape fixture ONLY; signature/authority enforced by router."""
    def encoded(doc):
        return base64.urlsafe_b64encode(json.dumps(doc).encode()).decode().rstrip("=")
    return f"{encoded({'alg':'HS256','typ':'JWT'})}.{encoded({'allowed_model_aliases':models})}.fixture"


@pytest.fixture(autouse=True)
def reset_cooldown(monkeypatch):
    monkeypatch.setattr(memory, "_embed_broken_until", 0.0)
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "broad-internal-bypass")
    assert memory.requests is not None


@pytest.mark.parametrize("token", ["", "opaque", "a.b", "a.@@@.c", "a.a.c", "a.bnVsbA.c"])
def test_absent_or_bad_claim_never_posts(monkeypatch, token):
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", token)
    monkeypatch.setattr(memory.requests, "post", lambda *_a, **_kw: pytest.fail("provider network"))
    assert memory.embed("pure fixture") is None
    assert memory._embed_broken_until == 0


@pytest.mark.parametrize("allowed", [None, [], "text-embedding-nomic-embed-text-v1.5",
                                      ["other/model"], ["other/model", None], [""]])
def test_missing_or_wrong_scope_refuses_embedding(monkeypatch, allowed):
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", synthetic_token(allowed))
    monkeypatch.setattr(memory.requests, "post", lambda *_a, **_kw: pytest.fail("provider network"))
    assert memory.embed("pure fixture") is None


def test_claim_scoped_embedding_uses_only_executor_identity(monkeypatch):
    token = synthetic_token([memory.EMBED_MODEL])
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", token)
    captured = {}
    class Stub:
        status_code = 200
        def json(self):
            return {"data": [{"embedding": [1.0, 0.0, 0.5]}]}
    def fake_post(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Stub()
    monkeypatch.setattr(memory.requests, "post", fake_post)
    assert memory.embed("fixture") == [1.0, 0.0, 0.5]
    assert captured["kwargs"]["headers"] == {"Authorization": f"Bearer {token}"}
    assert captured["kwargs"]["json"]["model"] == memory.EMBED_MODEL
    assert "broad-internal-bypass" not in repr(captured)


def test_no_executor_token_does_not_start_retry_sleep(monkeypatch):
    monkeypatch.delenv("HERMES_EXECUTOR_TOKEN", raising=False)
    monkeypatch.setattr(memory.requests, "post", lambda *_a, **_kw: pytest.fail("provider network"))
    monkeypatch.setattr(memory.time, "sleep", lambda *_a: pytest.fail("retry sleep"))
    assert memory.embed("fixture") is None


def test_new_scoped_embeds_do_not_remove_split_root_or_signed_corpus_checks():
    """Both inherited mainline scope and branch storage custody must survive."""
    import inspect

    code=inspect.getsource(memory)
    assert 'HERMES_EXECUTOR_TOKEN' in code
    assert 'ASSISTX_INTERNAL_SERVICE_TOKEN' not in code
    assert 'resolve_knowledge_roots(' in code
    assert 'safe_knowledge_files(KNOWLEDGE_READ_ROOT' in code
    assert 'safe_read_text(fp, 200000)' in code
    assert 'checked_write_path(KNOWLEDGE_WRITE_ROOT, self.path)' in code
    assert 'signed corpus mode forbids stale semantic index reads' in code
    assert 'signed corpus mode forbids unbound persisted semantic index' in code
