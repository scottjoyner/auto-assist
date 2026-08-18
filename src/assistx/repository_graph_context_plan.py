"""Two-tier bounded context plans for Graphify-assisted coding tasks."""

from __future__ import annotations

from dataclasses import dataclass

from .repository_graph import RepositoryGraphProjection
from .repository_graph_reranker import RankedFileCandidate, rank_graph_files


@dataclass(frozen=True)
class GraphContextPlan:
    seed_file: str
    primary: tuple[RankedFileCandidate, ...]
    expansion: tuple[RankedFileCandidate, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in (*self.primary, *self.expansion))


def build_graph_context_plan(
    projection: RepositoryGraphProjection,
    *,
    seed_file: str,
    task_text: str,
    primary_limit: int = 6,
    expansion_limit: int = 6,
    historical_change_scores: dict[str, float] | None = None,
) -> GraphContextPlan:
    """Build a precision-first shortlist plus bounded recall expansion.

    `primary` contains only depth-1 candidates. `expansion` is selected from the
    reranked depth-2 pool after removing anything already in `primary`. This makes
    the empirical depth tradeoff an explicit caller contract rather than an
    implicit recommendation hidden in documentation.
    """
    if primary_limit < 0 or expansion_limit < 0:
        raise ValueError("context limits must be non-negative")
    if primary_limit == 0 and expansion_limit == 0:
        return GraphContextPlan(seed_file=seed_file, primary=(), expansion=())

    primary = (
        rank_graph_files(
            projection,
            seed_file=seed_file,
            task_text=task_text,
            max_depth=1,
            limit=max(1, primary_limit),
            historical_change_scores=historical_change_scores,
        )[:primary_limit]
        if primary_limit
        else ()
    )
    primary_paths = {candidate.path for candidate in primary}

    if not expansion_limit:
        return GraphContextPlan(seed_file=seed_file, primary=tuple(primary), expansion=())

    expanded_ranked = rank_graph_files(
        projection,
        seed_file=seed_file,
        task_text=task_text,
        max_depth=2,
        limit=max(primary_limit + expansion_limit + 12, 20),
        historical_change_scores=historical_change_scores,
    )
    expansion = tuple(
        candidate
        for candidate in expanded_ranked
        if candidate.depth == 2 and candidate.path not in primary_paths
    )[:expansion_limit]
    return GraphContextPlan(
        seed_file=seed_file,
        primary=tuple(primary),
        expansion=expansion,
    )
