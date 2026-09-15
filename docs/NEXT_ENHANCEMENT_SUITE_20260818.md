# AssistX Next Enhancement Suite — 2026-08-18

## Purpose

Move the current starred-repo experiments from compatibility/evidence infrastructure into **measured agent-loop improvements** while preserving AssistX, bounded execution contracts, and operator approval as the sole authority boundaries.

This suite is intentionally downstream of the current experiment PRs (#28–#31) and auto-router cache-affinity PR #13. It should consume their interfaces and evidence contracts without making them implicit production dependencies.

## Suite objective

Prove that the new context, memory, tracing, and cache-affinity capabilities can improve actual coding-agent work in shadow or controlled canary mode before any production authority changes.

The suite has four execution lanes, ordered by dependency and practical value.

---

## Lane 1 — Graph-assisted scope discovery

### Goal

Use Graphify before bounded execution-contract creation to propose a small candidate `allowed_paths` set, then measure whether that advice reduces repository discovery cost while preserving task success.

### Required implementation

- Import/consume a commit-pinned Graphify projection through the existing disposable projection boundary.
- Use `repository_graph_scope_advisor` before `build_execution_contract()` rather than injecting graph context into an already-bounded Hermes prompt.
- Keep advice shadow-only at first: record recommended paths, do not modify the live contract.
- Correlate recommendations with eventual contract paths, executor-changed files, file reads, repository searches, and task acceptance.
- Record top-1/top-3/top-5 scope recall and precision.
- Record search/file-read count before first useful edit.
- Add narrow expansion-budget experiments (for example 8+0, 8+2, 8+4, 10+2, 10+4) rather than broad depth-2 dumps.
- Add stale-commit and missing-artifact fail-closed behavior.

### Acceptance gate

Before canary scope assistance:

1. no task-success regression;
2. no out-of-contract mutation path;
3. >=20% reduction in repository search/file-read churn on the selected coding corpus;
4. mean scope precision >=0.50;
5. recommendation recall improves over the current seed-only baseline;
6. zero stale-projection acceptance;
7. explicit operator approval.

---

## Lane 2 — Local-model context compression equivalence

### Goal

Run raw, lossless JSON, direct Headroom, and hybrid compression against the **same local model endpoint and same tasks**, measuring answer correctness and real latency rather than token savings alone.

### Required implementation

- Reuse `context_output_equivalence.py` and `context-output-equivalence-endpoint.py`.
- Add a repeatable corpus manifest with model ID, runtime ID, quant, context size, endpoint, seed/temperature, task IDs, and source commit.
- Run at least 10 repeated baseline/candidate cases per candidate variant.
- Record input tokens, compression time, TTFT if available, total latency, answer pass/fail, output equivalence rate, and retrieval/fallback events.
- Treat direct Headroom and hybrid as independently promotable variants.
- Expand adversarial cases to stack traces, diffs, code symbols, numeric anomalies, escaped data, and long tool outputs.
- Produce `assistx.experiment-artifact.v1` evidence through the common promotion pipeline.

### Acceptance gate

Before any context optimizer canary:

1. output-equivalence rate = 1.0 on the selected benchmark set;
2. zero fidelity failures;
3. >=15% input-token reduction;
4. no pass/score regression;
5. compression overhead is lower than saved prompt-processing time on at least one real local endpoint;
6. no mean-latency regression under the configured promotion policy;
7. explicit operator approval.

---

## Lane 3 — Procedural-memory held-out evaluation

### Goal

Turn cass-memory / procedural-memory imports into measured, outcome-linked advice rather than durable rules based on anecdotal success.

### Required implementation

- Import sanitized `cm context --json` evidence through the read-only cass adapter.
- Build held-out coding-task fixtures with known later outcomes.
- Correlate retrieved rules with task success, repeated-error reduction, and time-to-first-correct-plan.
- Record supported, contradicted, irrelevant, invalidated, and superseded rules separately.
- Add confidence decay / freshness evaluation.
- Keep rule application shadow-only until promotion gates pass.
- If the lane advances to Neo4j persistence, use explicit provenance, invalidation, and supersession state; no raw chain-of-thought storage.

### Acceptance gate

1. >=75% eligible-rule support rate;
2. >=10% repeated-error reduction;
3. no measurable task-success regression;
4. single-success, conflict, invalidation, and supersession fault gates all pass;
5. every promoted rule is traceable to source sessions/outcomes;
6. explicit operator approval.

---

## Lane 4 — Cache-affinity live shadow telemetry

### Goal

Supply the already-green auto-router cache-affinity shadow bridge with real runtime cache identities and measure whether affinity predicts lower TTFT without overriding admission or routing safety.

### Required implementation

- Define the runtime telemetry producer for model hash, quant, context size, runtime ID, session ID, and stable-prefix fingerprint.
- Emit complete candidate cache identities beside authoritative route decisions.
- Correlate route request ID and AssistX trace ID.
- Record authoritative candidate, affinity candidate, agreement, excluded ineligible candidates, fallback reason, TTFT/prefill where available, and cache-hit evidence where exposed.
- Never infer cache residency from provider configuration.
- Missing telemetry must remain a no-op.
- Preserve route authority: affinity may only become a tie-breaker among otherwise eligible/equivalent candidates after promotion.

### Acceptance gate

1. >=10% median TTFT reduction on the selected repeated-prefix/multi-turn corpus;
2. zero routing-safety regressions;
3. model-hash, quant, context, and runtime invalidation tests all pass;
4. no ineligible runtime can win through affinity;
5. explicit operator approval.

---

## Cross-lane evidence contract

Every lane must emit or be convertible into the common experiment/evidence pipeline:

```text
execution / shadow observation
    -> assistx.trace.v1 correlation
    -> reproducible experiment manifest
    -> baseline/candidate comparison
    -> operational metrics
    -> fault gates
    -> CanaryPromotionManifest
    -> operator approval
```

No lane may define a separate self-promotion mechanism.

## CI requirements

- Deterministic unit tests for every new adapter/policy/metric.
- At least one negative fixture that proves the candidate is blocked.
- Dedicated artifact output for every real upstream/fleet experiment.
- Feature/shadow-off behavior must remain equivalent to current production behavior.
- Any runtime-dependent test must fail clearly when its prerequisite endpoint/telemetry is unavailable; it must not fabricate success.

## Recommended execution order

### Phase A — offline / GitHub Actions

1. Graph scope-advisor narrow-budget sweep and historical proxy.
2. Procedural-memory held-out corpus and calibration improvements.
3. Compression equivalence corpus expansion with deterministic fake endpoint tests.
4. Cache telemetry schema + fixtures + route correlation tests.

### Phase B — reachable fleet

1. Context compression raw-vs-candidate local-model A/B.
2. Cache-affinity shadow telemetry + TTFT measurement.
3. Graph-assisted real coding-task scope shadow.
4. Procedural-memory real session correlation.

### Phase C — controlled canary

Only candidates with a green `CanaryPromotionManifest` and explicit operator approval may enter a canary. Canary scope should remain small, reversible, observable, and independently disableable per lane.

## Suite completion criteria

This enhancement suite is complete when:

- Graphify has task-level evidence for search/file-read reduction and a justified narrow scope-assistance budget;
- at least one context-compression variant has real local-model output-equivalence and latency evidence;
- procedural memory has held-out outcome-linked calibration rather than only shadow retrieval mechanics;
- cache affinity has real runtime telemetry and TTFT evidence;
- all results are represented in the common experiment/promotion pipeline;
- no feature receives behavioral authority without explicit canary approval.
