from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationSuiteDef:
    name: str
    agent_class: str
    enabled: bool
    cadence: str
    threshold: float
    description: str


@dataclass(frozen=True)
class HarnessSuiteDef:
    """A registered agent-harness benchmark suite (see the harness version
    registry in the knowledge base). Suite IDs and schema versions are separate
    fields; never infer one from the other."""

    id: str
    schema_version: str
    task_families: tuple[str, ...] = ()
    min_trials: int = 1
    source_repo: str = ""
    source_path: str = ""
    description: str = ""


# Built-in registry mirrors the harness version registry. The Hermes
# qualification suites require at least three valid trials; a single prompt or
# one-off probe is not a versioned benchmark result.
DEFAULT_HARNESS_SUITES: tuple[HarnessSuiteDef, ...] = (
    HarnessSuiteDef(
        id="hermes_agent_intelligence.v1",
        schema_version="hermes_agent_suite.v1",
        task_families=("tool_use", "reasoning"),
        min_trials=3,
        source_repo="lms",
        source_path="src/lms_agent_bench/benchmarks/hermes_agent_suite.v1.json",
        description="Eight Hermes MCP cases: tool selection, graph retrieval, file mutation, recovery, safety, effect scoring.",
    ),
    HarnessSuiteDef(
        id="hermes_agent_context_pressure.v1",
        schema_version="hermes_agent_suite.v1",
        task_families=("long_context",),
        min_trials=3,
        source_repo="lms",
        source_path="src/lms_agent_bench/benchmarks/hermes_agent_context_suite.v1.json",
        description="Two long-context Hermes cases with control-code retention and context-pressure gates.",
    ),
    HarnessSuiteDef(
        id="agent_skill_suite.v1",
        schema_version="2",
        task_families=("coding", "extraction", "summarization"),
        min_trials=1,
        source_repo="lms",
        source_path="src/lms_agent_bench/benchmarks/agent_skill_suite.v1.json",
        description="Nine LM Studio agent-skill cases: health, structured JSON, coding, debugging, planning, long context, repository work, safety.",
    ),
    HarnessSuiteDef(
        id="cpm-tb2-bench-v1",
        schema_version="baremetal tasks.json",
        task_families=("tool_use",),
        min_trials=1,
        source_repo="agent-harness",
        source_path="cpm_tb2_bench.py + benchmarks/baremetal/tasks.json",
        description="MiniCPM5-2B TB2.0-proxy baremetal loop: 20 tasks with file/command verification.",
    ),
    HarnessSuiteDef(
        id="terminal_bench_4.0",
        schema_version="4.0.0",
        task_families=("tool_use",),
        min_trials=1,
        source_repo="agent-harness",
        source_path="agent-harness benchmark --suite-id 4.0 (66-task manifest)",
        description="TerminalBench 4.0 docker-task suite via the agent-harness CLI.",
    ),
)


def _parse_env(raw: str) -> list[EvaluationSuiteDef]:
    # format: name|agent_class|enabled|cadence|threshold|description ; ...
    out: list[EvaluationSuiteDef] = []
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


def get_evaluation_suites() -> list[EvaluationSuiteDef]:
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


def suites_summary() -> dict[str, object]:
    suites = get_evaluation_suites()
    enabled = sum(1 for s in suites if s.enabled)
    by_agent_class: dict[str, int] = {}
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


def _parse_harness_env(raw: str) -> list[HarnessSuiteDef]:
    # JSON list of objects: id (required), schema_version, task_families,
    # min_trials, source_repo, source_path, description.
    out: list[HarnessSuiteDef] = []
    try:
        parsed = json.loads(raw)
    except Exception:
        return out
    if not isinstance(parsed, list):
        return out
    for item in parsed:
        if not isinstance(item, dict):
            continue
        suite_id = str(item.get("id") or "").strip()
        if not suite_id:
            continue
        families = item.get("task_families") or ()
        if isinstance(families, str):
            families = [families]
        out.append(
            HarnessSuiteDef(
                id=suite_id,
                schema_version=str(item.get("schema_version") or ""),
                task_families=tuple(
                    str(f).strip().lower() for f in families if str(f).strip()
                ),
                min_trials=max(1, int(item.get("min_trials") or 1)),
                source_repo=str(item.get("source_repo") or ""),
                source_path=str(item.get("source_path") or ""),
                description=str(item.get("description") or ""),
            )
        )
    return out


def get_harness_suites() -> list[HarnessSuiteDef]:
    """Registered agent-harness benchmark suites. Override or extend via the
    ASSISTX_HARNESS_SUITES env var (JSON list); invalid overrides fall back to
    the built-in registry."""
    raw = os.getenv("ASSISTX_HARNESS_SUITES", "").strip()
    if raw:
        parsed = _parse_harness_env(raw)
        if parsed:
            return parsed
    return list(DEFAULT_HARNESS_SUITES)


def suite_for_family(task_family: str) -> HarnessSuiteDef | None:
    """First registered suite covering ``task_family`` (declaration order is
    precedence: Hermes qualification suites win their families)."""
    family = (task_family or "").strip().lower()
    if not family:
        return None
    for suite in get_harness_suites():
        if family in suite.task_families:
            return suite
    return None
