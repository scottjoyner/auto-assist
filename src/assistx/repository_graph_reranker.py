"""Bounded file reranking for Graphify repository projections.

Depth-1 neighbors are treated as high-signal evidence. Depth-2 candidates are a
recall expansion pool and must earn context budget through relation strength,
task-term overlap, file-role affinity, and optional historical change evidence.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from .repository_graph import RepositoryGraphProjection

_RELATION_WEIGHT = {
    "imports": 1.00,
    "imports_from": 1.00,
    "calls": 0.95,
    "method": 0.90,
    "inherits": 0.90,
    "uses": 0.80,
    "references": 0.70,
    "re_exports": 0.70,
    "indirect_call": 0.60,
    "defines": 0.55,
    "contains": 0.20,
    "rationale_for": 0.15,
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class RankedFileCandidate:
    path: str
    depth: int
    score: float
    strongest_relation: str | None
    relation_score: float
    task_overlap: float
    role_affinity: float
    historical_score: float


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(value) if len(token) > 1}


def _role(path: str) -> str:
    normalized = path.lower()
    if "/tests/" in f"/{normalized}" or normalized.startswith("tests/") or "/test_" in normalized:
        return "test"
    if normalized.endswith(".py"):
        return "code"
    if normalized.endswith((".md", ".rst", ".txt")):
        return "docs"
    if normalized.endswith((".yml", ".yaml", ".toml", ".json")):
        return "config"
    return "other"


def _role_affinity(seed_path: str, candidate_path: str, task_terms: set[str]) -> float:
    seed_role = _role(seed_path)
    candidate_role = _role(candidate_path)
    if candidate_role == seed_role:
        return 1.0
    task_mentions_test = bool(task_terms & {"test", "tests", "pytest", "coverage", "ci"})
    if task_mentions_test and candidate_role == "test":
        return 1.0
    if {seed_role, candidate_role} == {"code", "test"}:
        return 0.75
    if candidate_role == "config" and bool(task_terms & {"config", "workflow", "ci", "deploy"}):
        return 0.80
    return 0.25


def rank_graph_files(
    projection: RepositoryGraphProjection,
    *,
    seed_file: str,
    task_text: str,
    max_depth: int = 2,
    limit: int = 12,
    historical_change_scores: dict[str, float] | None = None,
) -> tuple[RankedFileCandidate, ...]:
    """Rank bounded file candidates around `seed_file`.

    The function never reads files or mutates the graph. A candidate's score is a
    weighted combination of graph proximity/relation evidence, task/path lexical
    overlap, file-role affinity, and optional caller-supplied historical evidence.
    Depth-1 receives a strong prior; depth-2 is penalized to keep expansion bounded.
    """
    if max_depth not in {1, 2}:
        raise ValueError("max_depth must be 1 or 2")
    if limit < 1:
        raise ValueError("limit must be positive")

    node_file = {
        str(node["id"]): str(node.get("source_file") or "").strip()
        for node in projection.nodes
    }
    seed_nodes = {node_id for node_id, path in node_file.items() if path == seed_file}
    if not seed_nodes:
        return ()

    adjacency: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in projection.edges:
        source = str(edge["source"])
        target = str(edge["target"])
        relation = str(edge.get("relation") or "related_to")
        adjacency[source].append((target, relation))
        adjacency[target].append((source, relation))

    file_depth: dict[str, int] = {}
    file_relation_score: defaultdict[str, float] = defaultdict(float)
    file_relation: dict[str, str] = {}
    queue = deque((node_id, 0) for node_id in seed_nodes)
    seen_depth = {node_id: 0 for node_id in seed_nodes}

    while queue:
        node_id, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor, relation in adjacency.get(node_id, ()):
            next_depth = depth + 1
            previous = seen_depth.get(neighbor)
            if previous is None or next_depth < previous:
                seen_depth[neighbor] = next_depth
                queue.append((neighbor, next_depth))
            path = node_file.get(neighbor, "")
            if not path or path == seed_file:
                continue
            if path not in file_depth or next_depth < file_depth[path]:
                file_depth[path] = next_depth
            relation_score = _RELATION_WEIGHT.get(relation, 0.40)
            if relation_score > file_relation_score[path]:
                file_relation_score[path] = relation_score
                file_relation[path] = relation

    task_terms = _tokens(task_text)
    history = historical_change_scores or {}
    ranked: list[RankedFileCandidate] = []
    for path, depth in file_depth.items():
        path_terms = _tokens(path.replace("/", " ").replace(".", " "))
        task_overlap = len(task_terms & path_terms) / len(task_terms) if task_terms else 0.0
        role_affinity = _role_affinity(seed_file, path, task_terms)
        historical_score = max(0.0, min(1.0, float(history.get(path, 0.0))))
        relation_score = file_relation_score[path]
        depth_prior = 1.0 if depth == 1 else 0.45
        score = (
            0.35 * depth_prior
            + 0.25 * relation_score
            + 0.20 * task_overlap
            + 0.10 * role_affinity
            + 0.10 * historical_score
        )
        ranked.append(
            RankedFileCandidate(
                path=path,
                depth=depth,
                score=score,
                strongest_relation=file_relation.get(path),
                relation_score=relation_score,
                task_overlap=task_overlap,
                role_affinity=role_affinity,
                historical_score=historical_score,
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.depth, item.path))
    return tuple(ranked[:limit])


def paths(candidates: Iterable[RankedFileCandidate]) -> tuple[str, ...]:
    return tuple(candidate.path for candidate in candidates)
