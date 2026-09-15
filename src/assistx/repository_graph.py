"""Adapters for disposable repository-graph projections.

The first adapter consumes Graphify's documented NetworkX node-link JSON artifact
without making Graphify a runtime dependency or canonical data owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RepositoryGraphProjection:
    repository: str
    commit_sha: str
    nodes: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def normalize_graphify_graph(
    graph: dict[str, Any], *, repository: str, commit_sha: str
) -> RepositoryGraphProjection:
    """Normalize Graphify node-link JSON into a commit-pinned projection."""

    repository = _require_text(repository, "repository")
    commit_sha = _require_text(commit_sha, "commit_sha")
    raw_nodes = graph.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValueError("graph.nodes must be a list")

    raw_edges = graph.get("edges", graph.get("links", []))
    if not isinstance(raw_edges, list):
        raise ValueError("graph edges/links must be a list")

    namespace = f"{repository}@{commit_sha}"
    normalized_nodes: list[dict[str, Any]] = []
    known_ids: set[str] = set()

    for index, node in enumerate(raw_nodes):
        if not isinstance(node, dict):
            raise ValueError(f"graph.nodes[{index}] must be an object")
        source_id = _require_text(node.get("id"), f"graph.nodes[{index}].id")
        if source_id in known_ids:
            raise ValueError(f"duplicate Graphify node id: {source_id}")
        known_ids.add(source_id)
        normalized_nodes.append(
            {
                "id": f"{namespace}:{source_id}",
                "source_id": source_id,
                "label": str(node.get("label") or source_id),
                "file_type": node.get("file_type"),
                "source_file": node.get("source_file"),
                "repository": repository,
                "commit_sha": commit_sha,
                "projection_source": "graphify",
            }
        )

    normalized_edges: list[dict[str, Any]] = []
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            raise ValueError(f"graph.edges[{index}] must be an object")
        source = _require_text(edge.get("source"), f"graph.edges[{index}].source")
        target = _require_text(edge.get("target"), f"graph.edges[{index}].target")
        if source not in known_ids or target not in known_ids:
            raise ValueError(f"edge references unknown node: {source}->{target}")
        confidence = str(edge.get("confidence") or "EXTRACTED").upper()
        if confidence not in {"EXTRACTED", "INFERRED", "AMBIGUOUS"}:
            raise ValueError(f"unsupported edge confidence: {confidence}")
        normalized_edges.append(
            {
                "source": f"{namespace}:{source}",
                "target": f"{namespace}:{target}",
                "relation": str(edge.get("relation") or "related_to"),
                "confidence": confidence,
                "confidence_score": edge.get("confidence_score"),
                "source_file": edge.get("source_file"),
                "repository": repository,
                "commit_sha": commit_sha,
                "projection_source": "graphify",
            }
        )

    return RepositoryGraphProjection(
        repository=repository,
        commit_sha=commit_sha,
        nodes=tuple(normalized_nodes),
        edges=tuple(normalized_edges),
    )
