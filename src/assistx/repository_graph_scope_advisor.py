"""Graph-assisted scope discovery for bounded repository improvements.

This module lives *before* execution-contract creation. It may suggest candidate
allowed paths, but it never expands an existing contract and never mutates a live
agent prompt. Human/operator or policy code must still approve the final scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from .repository_graph import RepositoryGraphProjection
from .repository_graph_context_plan import build_graph_context_plan


@dataclass(frozen=True)
class ScopeAdvice:
    seed_file: str
    task_text: str
    candidate_paths: tuple[str, ...]
    primary_paths: tuple[str, ...]
    expansion_paths: tuple[str, ...]
    max_files: int


def advise_scope(
    projection: RepositoryGraphProjection,
    *,
    seed_file: str,
    task_text: str,
    max_files: int,
    expansion_budget: int = 2,
    historical_change_scores: dict[str, float] | None = None,
) -> ScopeAdvice:
    """Return a bounded candidate scope for an upstream contract builder.

    The seed is always first and counts against `max_files`. Remaining capacity is
    filled precision-first from depth-1, then by at most `expansion_budget`
    reranked depth-2 files. The returned tuple is advisory only.
    """
    if max_files < 1:
        raise ValueError("max_files must be positive")
    if expansion_budget < 0:
        raise ValueError("expansion_budget must be non-negative")

    remaining = max_files - 1
    if remaining == 0:
        return ScopeAdvice(
            seed_file=seed_file,
            task_text=task_text,
            candidate_paths=(seed_file,),
            primary_paths=(),
            expansion_paths=(),
            max_files=max_files,
        )

    expansion_limit = min(expansion_budget, remaining)
    primary_limit = max(0, remaining - expansion_limit)
    plan = build_graph_context_plan(
        projection,
        seed_file=seed_file,
        task_text=task_text,
        primary_limit=primary_limit,
        expansion_limit=expansion_limit,
        historical_change_scores=historical_change_scores,
    )

    primary = tuple(candidate.path for candidate in plan.primary)
    expansion = tuple(candidate.path for candidate in plan.expansion)
    ordered = tuple(dict.fromkeys((seed_file, *primary, *expansion)))[:max_files]
    return ScopeAdvice(
        seed_file=seed_file,
        task_text=task_text,
        candidate_paths=ordered,
        primary_paths=primary,
        expansion_paths=expansion,
        max_files=max_files,
    )
