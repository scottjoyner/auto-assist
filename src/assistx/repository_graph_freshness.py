"""Freshness checks for commit-pinned repository graph projections."""

from __future__ import annotations

from dataclasses import dataclass

from .repository_graph import RepositoryGraphProjection


@dataclass(frozen=True)
class ProjectionFreshness:
    repository_matches: bool
    commit_matches: bool

    @property
    def fresh(self) -> bool:
        return self.repository_matches and self.commit_matches


def check_projection_freshness(
    projection: RepositoryGraphProjection,
    *,
    repository: str,
    current_commit_sha: str,
) -> ProjectionFreshness:
    return ProjectionFreshness(
        repository_matches=projection.repository == repository,
        commit_matches=projection.commit_sha == current_commit_sha,
    )
