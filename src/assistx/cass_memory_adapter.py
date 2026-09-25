"""Read-only adapter for cass-memory `cm context --json` output.

The adapter imports procedural evidence only. It does not invoke cass-memory,
modify its playbook, or make cass-memory the AssistX canonical memory store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CassProceduralEvidence:
    bullet_id: str
    content: str
    category: str
    scope: str
    maturity: str
    helpful_count: int
    harmful_count: int
    source_sessions: tuple[str, ...]
    source_agents: tuple[str, ...]
    effective_score: float | None
    reasoning: str | None
    negative: bool

    @property
    def support(self) -> int:
        return self.helpful_count + self.harmful_count

    @property
    def observed_success_rate(self) -> float:
        if self.support == 0:
            return 0.0
        return self.helpful_count / self.support


def parse_cass_context(payload: dict[str, Any]) -> tuple[CassProceduralEvidence, ...]:
    """Parse cass-memory ContextResult relevantBullets + antiPatterns."""
    if not isinstance(payload, dict):
        raise ValueError("cass context payload must be an object")

    rows: list[tuple[dict[str, Any], bool]] = []
    for key, negative in (("relevantBullets", False), ("antiPatterns", True)):
        values = payload.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"cass context {key} must be a list")
        for item in values:
            if not isinstance(item, dict):
                raise ValueError(f"cass context {key} entries must be objects")
            rows.append((item, negative))

    evidence: list[CassProceduralEvidence] = []
    for item, negative in rows:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        bullet_id = str(item.get("id") or "").strip()
        if not bullet_id:
            raise ValueError("cass bullet id is required")
        helpful = int(item.get("helpfulCount") or 0)
        harmful = int(item.get("harmfulCount") or 0)
        if helpful < 0 or harmful < 0:
            raise ValueError("cass feedback counts must be non-negative")
        raw_score = item.get("effectiveScore")
        effective_score = float(raw_score) if isinstance(raw_score, (int, float)) else None
        source_sessions = tuple(str(v) for v in item.get("sourceSessions", []) if str(v).strip())
        source_agents = tuple(str(v) for v in item.get("sourceAgents", []) if str(v).strip())
        evidence.append(
            CassProceduralEvidence(
                bullet_id=bullet_id,
                content=content,
                category=str(item.get("category") or "uncategorized"),
                scope=str(item.get("scope") or "global"),
                maturity=str(item.get("maturity") or "candidate"),
                helpful_count=helpful,
                harmful_count=harmful,
                source_sessions=source_sessions,
                source_agents=source_agents,
                effective_score=effective_score,
                reasoning=(str(item["reasoning"]) if item.get("reasoning") is not None else None),
                negative=negative or bool(item.get("isNegative")) or item.get("type") == "anti-pattern",
            )
        )
    return tuple(evidence)
