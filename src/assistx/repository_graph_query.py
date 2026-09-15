"""Read-only query helpers for normalized repository graph projections."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .repository_graph import RepositoryGraphProjection


def adjacency(projection: RepositoryGraphProjection) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for edge in projection.edges:
        source = str(edge["source"])
        target = str(edge["target"])
        graph[source].add(target)
        graph[target].add(source)
    return graph


def neighborhood(
    projection: RepositoryGraphProjection, node_id: str, *, depth: int = 1
) -> tuple[dict[str, Any], ...]:
    if depth < 0:
        raise ValueError("depth must be non-negative")
    nodes = {str(node["id"]): node for node in projection.nodes}
    if node_id not in nodes:
        return ()
    graph = adjacency(projection)
    seen = {node_id}
    queue = deque([(node_id, 0)])
    ordered: list[str] = []
    while queue:
        current, level = queue.popleft()
        ordered.append(current)
        if level >= depth:
            continue
        for neighbor in sorted(graph.get(current, ())):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, level + 1))
    return tuple(nodes[node] for node in ordered)


def incoming_impact(
    projection: RepositoryGraphProjection, node_id: str, *, depth: int = 2
) -> tuple[str, ...]:
    """Return upstream dependents that can reach node_id through directed edges."""
    if depth < 0:
        raise ValueError("depth must be non-negative")
    reverse: dict[str, set[str]] = defaultdict(set)
    for edge in projection.edges:
        reverse[str(edge["target"])].add(str(edge["source"]))
    seen = {node_id}
    queue = deque([(node_id, 0)])
    impact: list[str] = []
    while queue:
        current, level = queue.popleft()
        if level >= depth:
            continue
        for parent in sorted(reverse.get(current, ())):
            if parent not in seen:
                seen.add(parent)
                impact.append(parent)
                queue.append((parent, level + 1))
    return tuple(impact)
