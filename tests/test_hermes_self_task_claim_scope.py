"""Hermetic negative tests; no outbound provider/model requests."""
from __future__ import annotations

import base64
import json

import pytest

from assistx.agents import hermes_agent_adapter as adapter


def fixture_jwt(aliases):
    # Deliberately unsigned fixture; the real router must verify signatures.
    def b64(data):
        return base64.urlsafe_b64encode(
            json.dumps(data).encode()
        ).decode().rstrip("=")
    return f"{b64({'alg':'HS256','typ':'JWT'})}.{b64({'allowed_model_aliases':aliases})}.fixture"


@pytest.mark.parametrize("executor", ["", "opaque", "a.b", "a.@@@.c", "a.a.c", "a.bnVsbA.c"])
def test_missing_or_bad_claim_never_uses_internal_token(monkeypatch, executor):
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", executor)
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "internal-privileged-bypass")
    monkeypatch.setattr(adapter.requests, "post", lambda *_a, **_kw: pytest.fail("network call"))
    r = adapter.call_self_task_llm("not-a-live-prompt", "local/demo")
    assert r["success"] is False
    assert r["output"] == ""
    assert r["elapsed"] == 0.0


@pytest.mark.parametrize("aliases", [[], None, "local/demo", [None], [""]])
def test_empty_or_malformed_model_allowlist_denied(monkeypatch, aliases):
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", fixture_jwt(aliases))
    monkeypatch.setattr(adapter.requests, "post", lambda *_a, **_kw: pytest.fail("network call"))
    assert adapter.call_self_task_llm("fixture", "local/demo")["error"] == "invalid_executor_claim_scope"


def test_claim_does_not_silently_remap_unapproved_model(monkeypatch):
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", fixture_jwt(["local/allowed"]))
    monkeypatch.setattr(adapter.requests, "post", lambda *_a, **_kw: pytest.fail("network call"))
    r = adapter.call_self_task_llm("fixture", "remote/not-in-claim")
    assert r["error"] == "model_outside_executor_claim_scope"


def test_valid_shape_forwards_claim_token_not_internal_identity(monkeypatch):
    token = fixture_jwt(["local/allowed"])
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", token)
    monkeypatch.setenv("ASSISTX_INTERNAL_SERVICE_TOKEN", "internal-must-never-forward")
    seen = {}
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"choices":[{"message":{"content":"fixture-result"}}],"usage":{"completion_tokens":1}}
    def fake_post(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return FakeResponse()
    monkeypatch.setattr(adapter.requests, "post", fake_post)
    r = adapter.call_self_task_llm("synthetic-only", "local/allowed")
    assert r["success"] is True
    assert r["output"] == "fixture-result"
    assert seen["headers"] == {"Authorization": f"Bearer {token}"}
    assert seen["json"]["model"] == "local/allowed"
    assert "internal-must-never-forward" not in repr(seen)
