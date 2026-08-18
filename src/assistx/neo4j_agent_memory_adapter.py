"""Optional adapter seam for Neo4j Agent Memory experiments.

The upstream package is lazy-loaded and this module does not perform writes. It
normalizes settings construction so an experiment can later use a dedicated graph
or explicit adopted schema without changing AssistX's canonical Neo4j model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class AgentMemorySettingsSpec:
    neo4j_uri: str
    neo4j_password: str
    neo4j_user: str = "neo4j"
    llm: str | None = None
    embedding: str | None = None

    def validate(self) -> None:
        if not self.neo4j_uri.strip():
            raise ValueError("neo4j_uri is required")
        if not self.neo4j_password:
            raise ValueError("neo4j_password is required")
        if not self.neo4j_user.strip():
            raise ValueError("neo4j_user is required")


def build_agent_memory_settings(
    spec: AgentMemorySettingsSpec,
    *,
    settings_factory: Callable[..., Any] | None = None,
) -> Any:
    """Construct upstream MemorySettings without opening a connection or writing."""
    spec.validate()
    if settings_factory is None:
        try:
            from neo4j_agent_memory import MemorySettings as settings_factory  # type: ignore[no-redef]
        except ImportError as exc:
            raise RuntimeError(
                "Neo4j Agent Memory is not installed; install `neo4j-agent-memory` only in the experiment environment"
            ) from exc

    kwargs: dict[str, Any] = {
        "neo4j": {
            "uri": spec.neo4j_uri,
            "user": spec.neo4j_user,
            "password": spec.neo4j_password,
        }
    }
    if spec.llm is not None:
        kwargs["llm"] = spec.llm
    if spec.embedding is not None:
        kwargs["embedding"] = spec.embedding
    return settings_factory(**kwargs)


def memory_context_request(
    *,
    query: str,
    session_id: str,
    user_identifier: str | None = None,
) -> dict[str, str]:
    """Normalize a future read-only `MemoryClient.get_context()` request."""
    if not query.strip():
        raise ValueError("query is required")
    if not session_id.strip():
        raise ValueError("session_id is required")
    payload = {"query": query, "session_id": session_id}
    if user_identifier is not None:
        if not user_identifier.strip():
            raise ValueError("user_identifier must be non-empty")
        payload["user_identifier"] = user_identifier
    return payload
