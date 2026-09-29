# Trace/Eval PoC — 2026-08-17

Issue: `#24`
Branch: `agent/trace-eval-poc`

## Hypothesis

AssistX can define a small normalized trace contract that is independent of any observability vendor, then evaluate stored traces deterministically in CI without model calls or network access.

## First implementation

Added `assistx.evaluation.trace_eval.evaluate_trace()` with seven deterministic gates:

1. task span present;
2. route span present;
3. model span present;
4. terminal outcome is successful;
5. retry count is bounded to two or fewer;
6. no route declares a public network path;
7. successful outcomes carry evidence IDs.

The evaluator returns a boolean pass/fail, normalized score, per-check results, and failure reasons.

## Negative fixtures/tests

The initial test set deliberately degrades traces to verify the gate catches:

- public-route selection;
- three-or-more retries;
- success without evidence;
- missing task/route/model spans;
- non-success terminal outcomes.

This is intentionally dependency-free so CI evaluation does not depend on Phoenix, TensorZero, Agenta, an LLM, Neo4j, or a running AssistX fleet.

## What this proves

If the test suite passes, it proves the normalized behavior-gate layer can distinguish known-good from deliberately degraded stored traces using pure deterministic logic.

It does **not** yet prove:

- live span instrumentation across AssistX/auto-router;
- OTLP export;
- Phoenix ingestion;
- TensorZero ingestion/replay;
- real fleet latency/token attributes;
- production privacy redaction.

Those are the next experiment stages.

## Next experiment

1. Add a versioned normalized trace schema and serializer.
2. Instrument one low-risk AssistX execution path with task/route/model/outcome spans.
3. Capture a sanitized trace fixture from a real run.
4. Export the same normalized run to OTLP.
5. Feed identical stored data to Phoenix/agentevals and TensorZero.
6. Compare deployment complexity, trace fidelity, deterministic evaluation support, replay, privacy controls, and compatibility with AssistX/auto-router authority.

## Promotion rule

No upstream observability platform becomes authoritative. AssistX owns the normalized behavioral contract; external tools are sinks, viewers, evaluators, or experiment runners. The winning integration must preserve local/offline operation and must not create a second routing/admission control plane.
