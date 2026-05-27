# Phase 10: Persistent Research and Technical Analyst Agent Fleet

Date: May 24, 2026  
Owner: AssistX orchestration + domain intelligence

## 1. Objective

Run always-on domain agents that continuously monitor, evaluate, and publish
high-signal outputs across research, trading context, and financial health.

## 2. Agent Classes

1. Persistent Research Agents
- Continuously ingest and summarize sector/company/macro changes.
- Track hypothesis threads and confidence updates over time.
- Publish watchlist and risk/opportunity deltas.

2. Technical Analyst Agents
- Evaluate trend/volatility/structure signals on configured symbols.
- Detect regime shifts and anomaly conditions.
- Produce ranked setups with confidence + invalidation criteria.

3. Financial Health Analyst Agents
- Evaluate liquidity, leverage, profitability, and cashflow quality.
- Flag deterioration/improvement trajectories.
- Maintain rolling issuer health snapshots and alerts.

## 3. Operating Model

```text
Feed Update/Event
  -> Domain Agent Trigger
      -> Analysis Workflow (draft/verify/escalate lanes)
          -> Evaluation Scoring
              -> Publish Advisory / Alert / Escalate to Review
```

## 4. Guardrails

1. Advisory-first posture:
- outputs are analysis signals, not automatic external execution.

2. Evidence requirements:
- every recommendation must include source references and confidence rationale.

3. Drift controls:
- if model/feed quality drops, agent mode downgrades to monitor-only.

4. Human escalation:
- high-impact signals require operator acknowledgment.

## 5. Phase 10 Deliverables

1. Agent templates for each domain class.
2. Continuous scheduling and heartbeat model for persistent agents.
3. Domain dashboards:
- research stream,
- technical signal board,
- financial health board.
4. Alert routing:
- threshold-based, severity-ranked, deduplicated.

## 6. Key Metrics

1. Signal precision/recall against historical benchmarks.
2. Time-to-detection for material events.
3. False-positive and stale-signal rates.
4. Analyst throughput and operator acceptance rate.

## 7. Exit Criteria

1. Always-on agents run continuously for 14 days with bounded failure rates.
2. Domain outputs remain evaluation-qualified above threshold.
3. Alert fatigue remains controlled (dedupe + severity gates effective).
4. Operator trust metrics improve (acceptance rate + reduced manual triage).
