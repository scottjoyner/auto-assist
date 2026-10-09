# Trace evidence inspection — observations and operator handoff

**Executed:** October 8, 2026 on x1-370. **Experiment:** preregistered in `TRACE_EVIDENCE_INSPECTION_PROSPECTUS_20261008.md` before the new code/tests. **Mode:** synthetic fixtures, no live trace data or production graph queries. **Branch:** `feature/assistx-trace-evidence-20261008`, stacked after draft #124.

## Problem
An operator investigating a trace may see recorded `task_id`, a mutable Task projection's `node_id`, and a same-string `SwarmNode.node_id`. These are *three different evidence levels*. An equality match is not proof that the real machine ran that work. The existing Task projection can get its worker/node values from an assignment event payload; the SwarmNode registry is not yet an independently attested source of execution identity.

## Predictions and observations
| Prediction | Observation |
| --- | --- |
| Only a task explicitly linked to a trace event may appear | **Pass (query contract):** `TraceGroup-[:HAS_EVENT]->TraceEvent-[:FOR_TASK]->Task`, further requiring `e.task_id=t.id`; name-only joins excluded |
| Bounded query and output | **Pass:** exact `$correlation_id`, 4-second timeout per statement, ordered `LIMIT 13` to return at most 12 tasks plus a truncation indicator, no payload/credential/IP projection |
| Missing/multiple registry matches do not imply node identity | **Pass:** four states `not_recorded`, `not_registered`, `registry_id_match_unverified`, `ambiguous_registry_id`; every result explicitly `node_or_agent_verified=false` |
| Optional manual, read-only UI query | **Pass:** no evidence call on trace load; exactly one new GET on clicking `Inspect task / registry evidence`. No remote links or dispatch controls |
| Safe legacy, invalid and stale data | **Pass:** missing context schema still offers explicit inspection; wrong evidence schema fails closed, stale response cannot replace another trace, HTML-shaped values are escaped |
| Existing trace UX unaffected | **Pass:** 20 Node VM tests, including the previous 15 UI cases, and 48 Python tests, including previous 32 context/outcome cases |
| Trusted execution attribution | **Not established and deliberately not claimed**. Source/node registry matching is unverified corroboration, not signed execution proof |

## Implementation
- `src/assistx/swarm_core.py`: new `get_trace_task_evidence()`. Checks trace existence, returns exact linked task projections and bounded `SwarmNode` ID registration match count. Does not return node IPs or any arbitrary registry properties. Task IDs/status, worker IDs and node IDs are sanitized/bounded to 128 printable characters.
- `src/assistx/swarm_routes.py`: new authenticated `GET /api/traces/{correlation_id}/evidence` (correlation ID max 128, 404 when no trace). Same existing injected authentication dependency as trace detail; no POST or write route added.
- `static/js/traces.js` and `static/css/traces.css`: explicit opt-in context inspection, unverified labels and missing/ambiguous handling, 12-task cap, no execution links, race fencing, escaping and no automatic new fetch.
- `tests/test_trace_task_evidence.py`: **16 new synthetic Python tests**, including multiple/unregistered/ambiguous IDs, absent trace, invalid route/path, projection claims, row caps, query budget, no payload projection and injected FastAPI auth.
- `tests/test_trace_investigation_ui.cjs`: **5 new synthetic Node tests** for no eager query, safe on-demand rendering, ambiguous registry, stale result fencing, and fail-closed older schema.

## Verification
Run from `/home/scott/git/wt-assistx-trace-evidence-20261008`:

```bash
PYTHONPATH=src python3 -m pytest -q \
  tests/test_trace_task_evidence.py \
  tests/test_trace_context.py \
  tests/test_trace_outcome_filter.py
node --check static/js/traces.js
node --test tests/test_trace_investigation_ui.cjs
python3 -m py_compile src/assistx/swarm_core.py src/assistx/swarm_routes.py
```

**Observed:** 48 passing Python tests and 20 passing Node VM UI tests. One pre-existing Starlette TestClient `httpx` deprecation warning. These counts include earlier suites, not additional 48+20 beyond prior coverage. No real Neo4j records, session/trace payloads, private log files or NAS archives were processed for this experiment.

## Remaining acceptance
1. **No cryptographic provenance.** The current registry is self-announced and Task node/worker projections are mutable event-payload products. Registry match merely corroborates exact ID strings; agent identity remains unverified.
2. **No live performance evidence.** Two on-demand statements have separate 4-second timeouts. Full fleet-sized query plans, indexes, concurrent requests and rate limits need staged measurement. No query was sent against live graph.
3. **No complete custody claim.** The router trace index remains separate from the August 8, 2025 full-fidelity nontrimming tool-call audit (issue #117). The UI must never describe it as a signed history ledger.
4. **No browser acceptance.** Responsive CSS and synthetic DOM logic passed tests, but authenticated desktop/mobile real-browser focus, screen-reader and visual review remain under issue #123.
5. **No operator action authority.** No SSH, remote execution, routing, provider, graph mutation, NAS operation or service restart was introduced.
6. **No deployment.** Review parent PRs in order #121 → #122 → #124, then this stacked PR, with rollback planning and a release decision. Issue #125 remains open until source-attested identity is independently established.

**Decision:** commit only as a separate read-only research draft. This closes the *UI capability to inspect recorded relationships*, not the *identity attestation* or *production release* gates.
