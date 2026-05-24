from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class EvaluationSuiteDef:
    name: str
    agent_class: str
    enabled: bool
    cadence: str
    threshold: float
    description: str


def _parse_env(raw: str) -> List[EvaluationSuiteDef]:
    # format: name|agent_class|enabled|cadence|threshold|description ; ...
    out: List[EvaluationSuiteDef] = []
    for item in [x.strip() for x in raw.split(";") if x.strip()]:
        parts = [p.strip() for p in item.split("|")]
        if len(parts) < 6:
            continue
        enabled = parts[2].lower() in {"1", "true", "yes", "on"}
        try:
            threshold = float(parts[4])
        except Exception:
            threshold = 0.8
        out.append(
            EvaluationSuiteDef(
                name=parts[0],
                agent_class=parts[1],
                enabled=enabled,
                cadence=parts[3],
                threshold=threshold,
                description=parts[5],
            )
        )
    return out


def get_evaluation_suites() -> List[EvaluationSuiteDef]:
    raw = os.getenv("ASSISTX_EVALUATION_SUITES", "").strip()
    if raw:
        parsed = _parse_env(raw)
        if parsed:
            return parsed
    return [
        EvaluationSuiteDef(
            name="financial_health_daily",
            agent_class="financial_health_analyst",
            enabled=True,
            cadence="daily",
            threshold=0.85,
            description="Daily financial health quality/regression suite.",
        ),
        EvaluationSuiteDef(
            name="research_quality_daily",
            agent_class="research_agent",
            enabled=True,
            cadence="daily",
            threshold=0.83,
            description="Daily research synthesis factuality and grounding checks.",
        ),
        EvaluationSuiteDef(
            name="sophia_auth_quality_daily",
            agent_class="voice_auth_analyst",
            enabled=True,
            cadence="daily",
            threshold=0.9,
            description="Sophia voice auth precision/recall and drift checks.",
        ),
        EvaluationSuiteDef(
            name="sophia_meeting_extraction_daily",
            agent_class="meeting_extraction_analyst",
            enabled=True,
            cadence="daily",
            threshold=0.82,
            description="Sophia meeting diarization/transcript/action-item extraction checks.",
        ),
    ]


def suites_summary() -> Dict[str, object]:
    suites = get_evaluation_suites()
    enabled = sum(1 for s in suites if s.enabled)
    by_agent_class: Dict[str, int] = {}
    for s in suites:
        by_agent_class[s.agent_class] = by_agent_class.get(s.agent_class, 0) + 1
    return {
        "total": len(suites),
        "enabled": enabled,
        "by_agent_class": by_agent_class,
        "suites": [
            {
                "name": s.name,
                "agent_class": s.agent_class,
                "enabled": s.enabled,
                "cadence": s.cadence,
                "threshold": s.threshold,
                "description": s.description,
            }
            for s in suites
        ],
    }
