#!/usr/bin/env python3
"""Validate the pinned Neo4j Agent Memory SDK surface without connecting to Neo4j."""

from __future__ import annotations

import json

from neo4j_agent_memory import MemoryClient, MemorySettings

from assistx.neo4j_agent_memory_adapter import (
    AgentMemorySettingsSpec,
    build_agent_memory_settings,
    memory_context_request,
)


def main() -> int:
    settings = build_agent_memory_settings(
        AgentMemorySettingsSpec(
            neo4j_uri="bolt://localhost:7687",
            neo4j_password="fixture-password",
            llm="ollama/qwen",
            embedding="BAAI/bge-small-en-v1.5",
        ),
        settings_factory=MemorySettings,
    )
    request = memory_context_request(
        query="Which prior coding procedure applies?",
        session_id="fixture-task",
        user_identifier="assistx",
    )
    client = MemoryClient(settings)
    if client is None:
        raise SystemExit("MemoryClient construction failed")
    print(
        json.dumps(
            {
                "memory_client_class": type(client).__name__,
                "settings_class": type(settings).__name__,
                "context_request_keys": sorted(request),
                "sdk_version": "0.5.0",
                "connected": False,
                "writes_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
