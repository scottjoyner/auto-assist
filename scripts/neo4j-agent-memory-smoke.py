#!/usr/bin/env python3
"""Validate pinned Neo4j Agent Memory SDK surface without providers or DB I/O."""

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
        ),
        settings_factory=MemorySettings,
    )
    request = memory_context_request(
        query="Which prior coding procedure applies?",
        session_id="fixture-task",
        user_identifier="assistx",
    )

    # Import/type compatibility is the boundary of this smoke. Constructing or
    # entering MemoryClient may initialize drivers/providers, which would violate
    # the explicit no-connection/no-provider contract of this CI experiment.
    if not isinstance(MemoryClient, type):
        raise SystemExit("MemoryClient is not an importable SDK class")

    print(
        json.dumps(
            {
                "memory_client_class": MemoryClient.__name__,
                "settings_class": type(settings).__name__,
                "neo4j_uri": str(settings.neo4j.uri),
                "context_request_keys": sorted(request),
                "sdk_version": "0.5.0",
                "provider_resolution_requested": False,
                "client_constructed": False,
                "connected": False,
                "writes_performed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
