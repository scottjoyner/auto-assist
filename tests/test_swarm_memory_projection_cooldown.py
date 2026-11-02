"""Fail-closed semantic-memory behavior when canonical runtime evidence expires."""
from __future__ import annotations

import base64
import json

import pytest
from dataclasses import dataclass
from types import SimpleNamespace

from assistx import swarm_memory


@pytest.fixture(autouse=True)
def scoped_claim(monkeypatch):
    # Only makes a structurally correct test token; no signature is asserted
    # by the client. Production router authenticates and authorizes the claim.
    payload = base64.urlsafe_b64encode(json.dumps({
        "allowed_model_aliases": [swarm_memory.EMBED_MODEL]
    }).encode()).decode().rstrip("=")
    monkeypatch.setenv("HERMES_EXECUTOR_TOKEN", f"header.{payload}.signature")


@dataclass
class Response:
    status_code: int
    body: dict

    def json(self):
        return self.body


def test_expired_projection_cooldown_skips_repeated_requests(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(503, {"detail": "canonical projection approval evidence is expired"})

    monkeypatch.setattr(swarm_memory, "requests", SimpleNamespace(post=post))
    monkeypatch.setattr(swarm_memory.time, "time", lambda: 1000.0)
    monkeypatch.setattr(swarm_memory, "_embed_broken_until", 0.0)
    assert swarm_memory.embed("one") is None
    assert swarm_memory.embed("two") is None
    assert len(calls) == 1
    assert swarm_memory._embed_broken_until == 1300.0
    assert calls[0][1]["json"]["input"] == "one"


def test_missing_runtime_projection_500_cools_down(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return Response(500, {"error": {
            "message": "signed AssistX runtime projection is not configured",
        }})

    monkeypatch.setattr(swarm_memory, "requests", SimpleNamespace(post=post))
    monkeypatch.setattr(swarm_memory.time, "time", lambda: 100.0)
    monkeypatch.setattr(swarm_memory, "_embed_broken_until", 0.0)
    assert swarm_memory.embed("a") is None
    assert swarm_memory.embed("b") is None
    assert len(calls) == 1


def test_transient_503_does_not_trigger_projection_cooldown(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return Response(503, {"detail": "temporary backend overload"})

    monkeypatch.setattr(swarm_memory, "requests", SimpleNamespace(post=post))
    monkeypatch.setattr(swarm_memory.time, "time", lambda: 100.0)
    monkeypatch.setattr(swarm_memory, "_embed_broken_until", 0.0)
    assert swarm_memory.embed("a") is None
    assert swarm_memory.embed("b") is None
    assert len(calls) == 2
    assert swarm_memory._embed_broken_until == 0.0


def test_cooldown_recovers_after_expiry(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        return Response(200, {"data": [{"embedding": [0.1, 0.2]}]})

    monkeypatch.setattr(swarm_memory, "requests", SimpleNamespace(post=post))
    monkeypatch.setattr(swarm_memory.time, "time", lambda: 2000.0)
    monkeypatch.setattr(swarm_memory, "_embed_broken_until", 1300.0)
    assert swarm_memory.embed("verified") == [0.1, 0.2]
    assert len(calls) == 1


def test_bulk_index_does_not_reset_projected_admission_cooldown(monkeypatch, tmp_path):
    monkeypatch.setattr(swarm_memory, "KNOWLEDGE_ROOT", str(tmp_path))
    monkeypatch.setattr(swarm_memory, "_embed_broken_until", 2000.0)
    monkeypatch.setattr(swarm_memory.time, "time", lambda: 1000.0)
    index = swarm_memory.MemoryIndex(path=str(tmp_path / ".memory" / "index.json"))
    assert index.index_vault() == 0
    assert swarm_memory._embed_broken_until == 2000.0
