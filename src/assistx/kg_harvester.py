"""Knowledge Graph Insight Harvester — generates LLM tasks from KG when idle.

When READY task count drops below threshold, the harvester queries the KG for
interesting patterns and creates LLM-capability tasks for the fleet executor
to dispatch to LM Studio nodes. This ensures fleet nodes are never idle —
they're always reading, analyzing, and generating insights.

Design:
  - Runs as a daemon thread alongside the fleet executor.
  - Checks READY task count every N seconds.
  - If below threshold, runs 5 insight archetypes in priority order.
  - Each creates 0-3 tasks per cycle, with idempotency keys to prevent dupes.
  - Archetypes are stateless and self-contained.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

from assistx.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

HARVEST_INTERVAL = float(os.getenv("KG_HARVEST_INTERVAL", "10"))
# The harvester keeps the LLM backlog topped up to at least this many READY
# tasks so fleet nodes are never starved between harvests.
TARGET_BACKLOG = int(os.getenv("KG_TARGET_BACKLOG", "200"))
READY_THRESHOLD = int(os.getenv("KG_READY_THRESHOLD", "100"))  # below this, harvest
MAX_TASKS_PER_CYCLE = int(os.getenv("KG_MAX_TASKS_PER_CYCLE", "10"))
MAX_PAPERS_PER_CYCLE = int(os.getenv("KG_MAX_PAPERS_PER_CYCLE", "10"))

BIG_MODELS = [
    "qwen3.6-35b-a3b-claude-4.7-opus-reasoning-distilled-apex-mtp",
    "qwen3.6-35b-a3b-claude-4.7-opus-reasoning-distilled-apex",
    "ornith-1.0-35b",
    "qwen3.5-27b-uncensored-heretic-v2-native-mtp-preserved",
    "qwen3.5-27b-claude-4.6-opus-reasoning-distilled",
]


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _hour_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")


def _hash_dict(d: dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]


class KgInsightHarvester:
    """Daemon that generates LLM insight tasks from KG when backlog is low."""

    def __init__(self) -> None:
        self._nc = Neo4jClient()
        self._batch: list[dict] = []

    # ── queries ──────────────────────────────────────────────

    def _ready_count(self) -> int:
        with self._nc.driver.session(database="assistx") as s:
            r = s.run(
                'MATCH (t:Task) WHERE t.status = "READY" RETURN count(t) AS c'
            ).single()
            return r["c"] if r else 0

    def _ready_llm_count(self) -> int:
        with self._nc.driver.session(database="assistx") as s:
            r = s.run(
                'MATCH (t:Task) WHERE t.status = "READY" '
                'AND "llm" IN coalesce(t.required_capabilities, []) '
                "RETURN count(t) AS c"
            ).single()
            return r["c"] if r else 0

    def _recent_papers(self, limit: int = 5) -> list[dict]:
        """Return Papers not yet analyzed (successfully) by a kg_insight task.

        Papers live in the default 'neo4j' database (written by arxiv_kg_bridge).
        Insight tasks live in 'assistx'. We fetch the set of already-DONE
        analyzed arxiv IDs from their idempotency keys (kg-insight/paper/{arxiv_id})
        and filter in Python. FAILED papers are retried (not skipped), so only
        DONE counts as consumed.
        """
        analyzed_ids: set[str] = set()
        with self._nc.driver.session(database="assistx") as s:
            rows = s.run(
                """
                MATCH (t:Task)
                WHERE t.kind = 'kg_insight'
                  AND t.status = 'DONE'
                  AND t.idempotency_key STARTS WITH 'kg-insight/paper/'
                RETURN t.idempotency_key AS ik
                """
            ).data()
            prefix = "kg-insight/paper/"
            for r in rows:
                ik = r["ik"] or ""
                if ik.startswith(prefix):
                    arxiv_id = ik[len(prefix):]
                    # Normalize by stripping version suffix (v1, v2, etc.)
                    if arxiv_id:
                        base_id = arxiv_id.split('v')[0]
                        analyzed_ids.add(base_id)

        # Fetch a generous batch (max 500) to skip past already-analyzed papers.
        # With ~83 analyzed so far, 500 covers ~400 new papers per cycle.
        fetch_limit = max(limit * 10, 200)
        fetch_limit = min(fetch_limit, 500)
        with self._nc.driver.session(database="neo4j") as s:
            rows = s.run(
                """
                MATCH (p:Paper)
                WHERE p.abstract IS NOT NULL
                RETURN p.arxiv_id AS id, p.title AS title,
                       p.abstract AS abstract, p.authors AS authors,
                       p.published AS published
                ORDER BY p.published DESC
                LIMIT $limit
                """,
                {"limit": fetch_limit},
            ).data()

        result = []
        for r in rows:
            pid = (r["id"] or "").strip()
            if not pid or pid in analyzed_ids:
                continue
            result.append({
                "id": pid,
                "title": (r["title"] or "").strip(),
                "abstract": r.get("abstract"),
                "authors": r.get("authors", []),
                "published": r.get("published", ""),
            })
            if len(result) >= limit:
                break

        return result

    def _memory_clusters(self) -> list[dict]:
        """Find MemoryItem clusters (items sharing kind/source)."""
        with self._nc.driver.session(database="assistx") as s:
            rows = s.run(
                """
                MATCH (m:MemoryItem)
                WHERE m.created_at_ts > $since
                WITH m.kind AS kind, collect(m) AS items
                WHERE size(items) >= 2
                RETURN kind, [x IN items | x.text][..5] AS samples,
                       size(items) AS count
                ORDER BY count DESC
                LIMIT 3
                """,
                {"since": _now_ts() - 86400_000},  # last 24h
            ).data()
            return [
                {
                    "kind": r["kind"] or "uncategorized",
                    "samples": [str(s) for s in (r["samples"] or [])],
                    "count": r["count"],
                }
                for r in rows
            ]

    def _signal_trends(self) -> list[dict]:
        """Aggregate signal events from the last hour."""
        with self._nc.driver.session(database="assistx") as s:
            rows = s.run(
                """
                MATCH (ev:SignalEvent)
                WHERE ev.created_at_ts > $since
                RETURN ev.event_type AS event_type,
                       ev.severity AS severity,
                       count(*) AS count
                ORDER BY count DESC
                LIMIT 10
                """,
                {"since": _now_ts() - 3600_000},
            ).data()
            return rows

    def _failed_tasks(self, limit: int = 5) -> list[dict]:
        """Recent FAILED tasks with their payloads."""
        with self._nc.driver.session(database="assistx") as s:
            rows = s.run(
                """
                MATCH (t:Task)
                WHERE t.status = 'FAILED'
                  AND t.completed_at_ts > $since
                OPTIONAL MATCH (t)<-[:EXECUTED_BY]-(r:AgentRun)
                RETURN t.id AS id, t.title AS title,
                       t.payload_json AS payload_json,
                       t.result_json AS result_json,
                       t.completed_at_ts AS completed_at,
                       r.id AS run_id, r.summary AS run_summary,
                       r.result_json AS run_result
                ORDER BY t.completed_at_ts DESC
                LIMIT $limit
                """,
                {"since": _now_ts() - 86400_000, "limit": limit},
            ).data()
            return rows

    def _task_throughput(self, limit: int = 10) -> list[dict]:
        """Recent task completion patterns by agent."""
        with self._nc.driver.session(database="assistx") as s:
            rows = s.run(
                """
                MATCH (t:Task)
                WHERE t.completed_at_ts > $since
                  AND t.status IN ['DONE', 'FAILED']
                RETURN t.status AS status, t.kind AS kind,
                       count(*) AS count,
                       avg(t.completed_at_ts - t.created_at_ts) / 1000.0 AS avg_duration_s
                ORDER BY count DESC
                LIMIT $limit
                """,
                {"since": _now_ts() - 86400_000, "limit": limit},
            ).data()
            return rows

    # ── task creation ────────────────────────────────────────

    def _create_llm_task(
        self,
        title: str,
        messages: list[dict],
        model_hint: str = "",
        idempotency_key: str = "",
    ) -> None:
        self._batch.append(
            {
                "title": title,
                "kind": "kg_insight",
                "required_capabilities": ["llm"],
                "priority": "background",
                "payload": {
                    "model": model_hint,
                    "messages": messages,
                    "prompt": messages[-1]["content"] if messages else "",
                    "harvester": "kg_insight",
                },
                "idempotency_key": idempotency_key or None,
            }
        )

    def _create_tasks_for_system_prompt(
        self,
        system_prompt: str,
        user_prompt: str,
        title: str,
        ik_suffix: str,
        model_hint: str = "",
    ) -> None:
        ik = f"kg-insight/{ik_suffix}/{_hour_bucket()}"
        self._create_llm_task(
            title=title,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model_hint=model_hint,
            idempotency_key=ik,
        )

    def _flush_batch(self) -> int:
        """Write all buffered tasks to Neo4j in a single session."""
        if not self._batch:
            return 0
        batch = self._batch
        self._batch = []
        try:
            return self._nc.create_tasks_batch(batch)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("kg harvester: batch write failed: %s", e)
            return 0

    # ── archetypes ───────────────────────────────────────────

    def _archetype_papers(self) -> int:
        """Read unanalyzed papers and generate research insights."""
        papers = self._recent_papers(MAX_PAPERS_PER_CYCLE)
        created = 0
        for paper in papers:
            title = paper.get("title") or "Untitled"
            abstract = (paper.get("abstract") or "")[:4000]
            authors = ", ".join((paper.get("authors") or [])[:3])
            published = (paper.get("published") or "unknown")[:10]

            content = (
                f"Title: {title}\n"
                f"Authors: {authors}\n"
                f"Published: {published}\n\n"
                f"Abstract: {abstract}"
            )

            self._create_llm_task(
                title=f"Paper: {title[:80]}",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a senior research engineer analyzing academic papers "
                            "for practical application. Given a paper abstract, produce:\n"
                            "1. KEY INSIGHT — the single most important finding (1 sentence)\n"
                            "2. RELEVANCE — how this applies to our autonomous agent stack "
                            "(knowledge graphs, LLM routing, fleet orchestration, voice AI)\n"
                            "3. IMPLEMENT — specific code, architecture, or integration "
                            "we should build based on this paper\n"
                            "4. QUESTIONS — open questions to research next\n\n"
                            "Be specific and actionable. Avoid generic advice."
                        ),
                    },
                    {"role": "user", "content": content},
                ],
                model_hint="",
                idempotency_key=f"kg-insight/paper/{paper['id']}",
            )
            created += 1
            logger.info(
                "kg harvester: paper task %s (%s)", paper["id"], title[:50]
            )

        return created

    def _archetype_memories(self) -> int:
        """Synthesize related memory items into insights (one task per cluster)."""
        clusters = self._memory_clusters()
        if not clusters:
            return 0
        created = 0
        for cl in clusters[:5]:
            prompt_lines = [
                f"Kind: {cl['kind']} ({cl['count']} items)",
            ]
            for s in cl["samples"]:
                prompt_lines.append(f"  - {str(s)[:200]}")
            self._create_tasks_for_system_prompt(
                system_prompt=(
                    "You are a knowledge synthesis engine. Given a cluster of memory items, "
                    "identify:\n"
                    "1. CROSS-CUTTING THEMES — what patterns span the items\n"
                    "2. CONTRADICTIONS — conflicting information that needs resolution\n"
                    "3. GAPS — what's missing that we should investigate\n"
                    "4. ACTIONABLE INSIGHTS — specific next steps to deepen understanding\n\n"
                    "Think step by step."
                ),
                user_prompt="\n".join(prompt_lines),
                title=f"Memory Synthesis [{cl['kind']}]: insight extraction",
                ik_suffix=f"memory-{cl['kind']}",
                model_hint="",
            )
            created += 1
        return created

    def _archetype_signals(self) -> int:
        """Detect and analyze signal event patterns (one task per event type)."""
        trends = self._signal_trends()
        if not trends:
            return 0
        created = 0
        for t in trends[:5]:
            lines = [
                f"Signal Event Type: {t.get('event_type','?')}",
                f"Severity: {t.get('severity','?')}",
                f"Count (last hour): {t['count']}",
                "",
                "Analyze this signal stream for:",
                "1. ANOMALIES — unusual patterns or spikes",
                "2. TRENDS — gradual changes over time",
                "3. HEALTH — what this says about system stability",
                "4. ACTIONS — specific alerts or configuration changes needed",
            ]
            self._create_tasks_for_system_prompt(
                system_prompt=(
                    "You are a system monitoring analyst. Given a single signal event type's "
                    "aggregates, identify anomalies, trends, health implications, and actions."
                ),
                user_prompt="\n".join(lines),
                title=f"Signal Analysis [{t.get('event_type','?')}]: system health",
                ik_suffix=f"signal-{t.get('event_type','unknown')}",
                model_hint="",
            )
            created += 1
        return created

    def _archetype_failed(self) -> int:
        """Autopsy recent failed tasks (one task per failed task)."""
        tasks = self._failed_tasks(5)
        if not tasks:
            return 0
        created = 0
        for t in tasks:
            lines = [
                f"Task: {t.get('title','?')}",
                f"Payload: {str(t.get('payload_json', ''))[:300]}",
                f"Result: {str(t.get('result_json', ''))[:300]}",
                f"Run: {str(t.get('run_summary', ''))[:300]}",
            ]
            self._create_tasks_for_system_prompt(
                system_prompt=(
                    "You are a root-cause analysis expert. For this failed task:\n"
                    "1. Determine the likely failure mode (timeout, model error, bad input, bug)\n"
                    "2. Suggest a specific fix (code change, config change, retry strategy)\n"
                    "3. Decide if the task should be retried as-is or needs modification\n\n"
                    "Be concise and practical."
                ),
                user_prompt="\n".join(lines),
                title=f"Failed Task Autopsy: {t.get('title','?')[:50]}",
                ik_suffix=f"failed-{t.get('id','x')}",
                model_hint="",
            )
            created += 1
        return created

    def _archetype_model_perf(self) -> int:
        """Analyze task execution throughput and patterns."""
        tasks = self._task_throughput(10)
        if not tasks:
            return 0

        lines = ["Task Execution Patterns (last 24h):\n"]
        for t in tasks:
            lines.append(
                f"  Status: {t.get('status','?')}  "
                f"Kind: {t.get('kind','?')}  "
                f"Count: {t['count']}  "
                f"Avg Duration: {t.get('avg_duration_s',0):.1f}s\n"
            )

        self._create_tasks_for_system_prompt(
            system_prompt=(
                "You are a workflow efficiency analyst. Given task execution data:\n"
                "1. Identify bottlenecks — which task types take longest\n"
                "2. Spot failure patterns — which kinds fail most often\n"
                "3. Recommend optimizations — parallelism, timeouts, retry strategies\n"
                "4. Suggest task priority rebalancing if needed\n\n"
                "Be concise and data-driven."
            ),
            user_prompt="\n".join(lines),
            title="Task Throughput Analysis: execution patterns and optimization",
            ik_suffix="throughput",
        )
        return 1

    # ── cycle ────────────────────────────────────────────────

    def harvest_cycle(self) -> int:
        """One harvest cycle. Archetypes queue tasks; all are written in a
        single batched Neo4j session at the end (to avoid saturating the
        connection pool). Returns the number of tasks actually written."""
        archetypes = [
            ("papers", self._archetype_papers),
            ("memories", self._archetype_memories),
            ("signals", self._archetype_signals),
            ("failed", self._archetype_failed),
            ("model_perf", self._archetype_model_perf),
        ]

        queued = 0
        for name, fn in archetypes:
            if queued >= MAX_TASKS_PER_CYCLE:
                logger.info("kg harvester: hit max tasks (%s), stopping", MAX_TASKS_PER_CYCLE)
                break
            try:
                created = fn()
                queued += created
                if created:
                    logger.info("kg harvester: %s -> %s tasks", name, created)
            except Exception as e:
                logger.warning("kg harvester: archetype %s failed: %s", name, e)

        written = self._flush_batch()
        if written:
            logger.info("kg harvester: wrote %s insight tasks this cycle", written)
        return written

    def harvest_until_target(self) -> None:
        """One harvest pass per tick. The fleet drains tasks between ticks; the
        next tick refills. We avoid tight refill loops because each pass hits
        Neo4j hard and a multi-pass loop overloads the connection pool, which
        then defuncts and stalls the whole fleet."""
        self.harvest_cycle()

    def close(self) -> None:
        try:
            self._nc.close()
        except Exception:
            pass


def _start_harvester_loop() -> None:
    """Start the daemon background thread."""

    def _loop() -> None:
        harvester = KgInsightHarvester()
        time.sleep(25)  # stagger behind fleet executor
        logger.info(
            "kg harvester: starting loop (every %ss, threshold=%s)",
            HARVEST_INTERVAL, READY_THRESHOLD,
        )
        while True:
            try:
                harvester.harvest_until_target()
            except Exception as e:
                logger.warning("kg harvester: cycle error: %s", e)
            time.sleep(HARVEST_INTERVAL)

    t = threading.Thread(target=_loop, name="kg-harvester", daemon=True)
    t.start()
    logger.info("kg harvester: daemon thread started")
