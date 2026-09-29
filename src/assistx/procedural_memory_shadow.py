"""Shadow retrieval for procedural-memory candidates.

Candidates are matched and scored for evidence collection without being injected
into the active agent context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .procedural_memory import ProceduralMemoryCandidate, eligible_for_promotion


@dataclass(frozen=True)
class ProceduralShadowMatch:
    rule: str
    score: float
    eligible: bool
    source_run_ids: tuple[str, ...]


def _tokens(text: str) -> set[str]:
    return {token.strip(".,:;()[]{}!?\"'").lower() for token in text.split() if token.strip()}


def shadow_match(
    task_text: str,
    candidates: Iterable[ProceduralMemoryCandidate],
    *,
    limit: int = 5,
) -> tuple[ProceduralShadowMatch, ...]:
    task_tokens = _tokens(task_text)
    matches: list[ProceduralShadowMatch] = []
    for candidate in candidates:
        rule_tokens = _tokens(candidate.rule)
        overlap = len(task_tokens & rule_tokens)
        union = len(task_tokens | rule_tokens)
        lexical = overlap / union if union else 0.0
        score = lexical * 0.5 + candidate.success_rate * 0.3 + candidate.confidence * 0.2
        matches.append(
            ProceduralShadowMatch(
                rule=candidate.rule,
                score=score,
                eligible=eligible_for_promotion(candidate),
                source_run_ids=candidate.source_run_ids,
            )
        )
    matches.sort(key=lambda item: (item.score, item.rule), reverse=True)
    return tuple(matches[: max(0, limit)])
