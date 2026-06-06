from fastapi.testclient import TestClient

from assistx import runtime as runtime_mod
from assistx.api import app


def _set_production_llm_env(monkeypatch):
    monkeypatch.setenv("ASSISTX_RUNTIME_PROFILE", "production")
    monkeypatch.setenv("ASSISTX_DEPENDENCY_MODE", "production")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "super-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://lmstudio.local:1234/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen3.5-0.8b")
    monkeypatch.setenv("EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("LLM_BACKEND", "openai")


def test_runtime_health_snapshots_dependencies_and_configuration(monkeypatch):
    _set_production_llm_env(monkeypatch)
    monkeypatch.setattr(runtime_mod, "_check_redis", lambda: {"status": "ok", "url": "redis://example"})
    monkeypatch.setattr(runtime_mod, "_check_neo4j", lambda: {"status": "ok", "uri": "bolt://example", "database": "assistx"})
    monkeypatch.setattr(runtime_mod, "_check_llm", lambda: {"status": "degraded", "backend": "openai", "endpoint": "http://llm"})

    health = runtime_mod.build_runtime_health()

    assert health["ok"] is True
    assert health["status"] == "ok"
    assert health["profile"] == "production"
    assert health["configuration"]["ok"] is True
    assert health["configuration"]["issues"] == []
    assert health["dependencies"]["redis"]["status"] == "ok"
    assert health["dependencies"]["neo4j"]["status"] == "ok"
    assert health["dependencies"]["llm"]["status"] == "degraded"


def test_runtime_configuration_rejects_ollama_in_production(monkeypatch):
    _set_production_llm_env(monkeypatch)
    monkeypatch.setenv("LLM_BACKEND", "ollama")

    config = runtime_mod.build_runtime_configuration()

    assert config["ok"] is False
    assert any(item["field"] == "LLM_BACKEND" for item in config["issues"])
    try:
        runtime_mod.validate_runtime_configuration(strict=True)
    except RuntimeError as exc:
        assert "LLM_BACKEND" in str(exc)
    else:
        raise AssertionError("expected validation failure")


def test_health_endpoint_returns_503_when_runtime_unhealthy(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr("assistx.api.build_runtime_health", lambda: {"ok": False, "status": "degraded", "profile": "production", "dependency_mode": "production", "timestamp": 1, "configuration": {"ok": False, "issues": [{"field": "OPENAI_BASE_URL", "reason": "missing"}]}, "dependencies": {"redis": {"status": "down"}, "neo4j": {"status": "ok"}, "llm": {"status": "ok"}}})

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["configuration"]["ok"] is False
    assert body["dependencies"]["redis"]["status"] == "down"
