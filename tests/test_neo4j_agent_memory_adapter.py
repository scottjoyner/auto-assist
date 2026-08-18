from types import SimpleNamespace

import pytest

from assistx.neo4j_agent_memory_adapter import (
    AgentMemorySettingsSpec,
    build_agent_memory_settings,
    memory_context_request,
)


def fake_settings_factory(**kwargs):
    return SimpleNamespace(**kwargs)


def test_settings_adapter_builds_self_hosted_bolt_configuration_without_connecting():
    settings = build_agent_memory_settings(
        AgentMemorySettingsSpec(
            neo4j_uri="bolt://localhost:7687",
            neo4j_password="fixture-password",
            llm="ollama/qwen",
            embedding="BAAI/bge-small-en-v1.5",
        ),
        settings_factory=fake_settings_factory,
    )
    assert settings.neo4j["uri"] == "bolt://localhost:7687"
    assert settings.neo4j["user"] == "neo4j"
    assert settings.neo4j["password"] == "fixture-password"
    assert settings.llm == "ollama/qwen"
    assert settings.embedding == "BAAI/bge-small-en-v1.5"


def test_settings_adapter_requires_explicit_database_target():
    with pytest.raises(ValueError, match="neo4j_uri"):
        build_agent_memory_settings(
            AgentMemorySettingsSpec(neo4j_uri="", neo4j_password="secret"),
            settings_factory=fake_settings_factory,
        )


def test_context_request_is_read_only_payload_shape():
    request = memory_context_request(
        query="Which procedure matched this coding task?",
        session_id="task-123",
        user_identifier="assistx",
    )
    assert request == {
        "query": "Which procedure matched this coding task?",
        "session_id": "task-123",
        "user_identifier": "assistx",
    }
