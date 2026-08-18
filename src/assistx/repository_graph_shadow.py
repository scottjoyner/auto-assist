"""Shadow scoring for repository-graph suggestions.

Graph suggestions are compared with files/tests actually used by an agent, without
injecting graph context into the live prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class GraphShadowScore:
    suggested: tuple[str, ...]
    actual: tuple[str, ...]
    hits: tuple[str, ...]
    precision: float
    recall: float


def score_suggestions(
    suggested: Iterable[str],
    actual: Iterable[str],
) -> GraphShadowScore:
    suggested_unique = tuple(dict.fromkeys(str(v) for v in suggested if str(v)))
    actual_unique = tuple(dict.fromkeys(str(v) for v in actual if str(v)))
    suggested_set = set(suggested_unique)
    actual_set = set(actual_unique)
    hits = tuple(sorted(suggested_set & actual_set))
    precision = len(hits) / len(suggested_set) if suggested_set else 0.0
    recall = len(hits) / len(actual_set) if actual_set else 0.0
    return GraphShadowScore(
        suggested=suggested_unique,
        actual=actual_unique,
        hits=hits,
        precision=precision,
        recall=recall,
    )
