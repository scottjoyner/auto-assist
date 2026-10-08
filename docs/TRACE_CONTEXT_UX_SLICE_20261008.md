# Preregistered slice — AssistX trace-context navigation (October 8, 2026)

**Purpose:** connect the existing trace event timeline to trustworthy, explicitly scoped task, dispatch, route, assignment, and producer source context without implying an asserted relationship to a live machine or authorizing an action.

**Branch:** `feature/assistx-trace-context-20261008` stacked on global-outcome PR #122 and trace-workbench PR #121. **No production access or deployment.**

## Source facts discovered before predicting

`record_trace_event()` already persists scalar `source`, `task_id`, `dispatch_id`, `route_id`, and `assignment_id` on TraceEvent nodes. `get_trace()` returns those top-level fields with the event timeline. Existing `_build_trace_summary()` can report `worker` / `node_id` extracted from *assignment payloads* (not independently authenticated). No verified universal node/agent mapping from every TraceEvent was found.

## Falsifiable predictions

1. A read-only `context` object derived **only from top-level scalar TraceEvent properties** will correctly enumerate distinct task/dispatch/route/assignment IDs and producing sources within a trace. No event payload JSON is parsed by the new context code.
2. For each field we can label its provenance `trace_event_property`, the number of supporting events and the first/last event index. Conflicting values remain separate; *multiple tasks in one trace must never be silently collapsed to one*.
3. Missing values are reported as `not_recorded`, not invented. `node_id` or `worker_id` in existing summary remains explicitly `assignment_payload_unverified`, never a claim of confirmed executor identity. Avoid pretending a source_repo label identifies an agent or physical machine.
4. The UI shows an investigation-context panel with safe labels, expandable individual fields, and buttons to show/highlight only events in the **already loaded timeline** carrying the selected top-level value. Reset restores all events. Neither filters nor context navigation make new server requests or issue tool commands.
5. Defensive validation: cap context values and field lengths, reject nested objects and unsafe scalar representations, escape all displayed text, do not insert private payloads into DOM or URL, and do not add dynamic links to nonexistent agent/task pages.
6. Existing global outcome search, selected trace permalink, keyboard selection, and read-only API behavior remain intact; unit and VM UI tests must cover missing fields, multiple IDs, provenance, malicious IDs and reset behavior.

## Non-goals and no-go

No new Graph traversal or global relationship query; no speculative node resolution; no Neo4j mutations, trace source decoding, NAS reads, agent execution, auth changes, provider admission changes, or live service restart. **The claim is context recorded on existing events, not verified identity or complete audit custody.** Browser/performance release gates on #123 remain open. This is an independently reviewable, stacked draft.

## Observations — October 8, 2026

**Source fact:** `record_trace_event()` stores top-level `source`, `task_id`, `dispatch_id`, `route_id`, and `assignment_id` scalar properties on each TraceEvent. The existing `get_trace()` already reads those events once and returns them under the authenticated detail endpoint. A `node_id` or `worker_id` may appear in the *old derived summary*, but it comes from assignment payload JSON and is **not authenticated node/agent custody**.

**Implementation:** added `_build_trace_context()` to the existing detail response, using top-level scalar event properties only. It records distinct values, supporting event count, first/last event indices and `trace_event_property` provenance. No additional graph query, node traversal, dynamic link, or payload parsing is introduced by the new helper. Up to 12 values/field and 160 characters/value are retained; malformed strings, control characters and nested values are discarded. Missing data is explicit. `node_or_agent_verified=false`, `payload_inspected=false`.

The workbench now renders a responsive **Recorded context** panel. Each entry is a keyboard-focusable button for filtering the **currently loaded** timeline to events with that same top-level value. The action creates no additional network calls. An **All trace events** control restores the original timeline, and the parent event-detail disclosures still defer inserting event payload text until explicitly opened. All inserted labels are escaped. If a backend has not deployed the context schema, the panel says context metadata is unavailable rather than inventing relationships. It warns that producer-source strings do not establish physical node or agent identity.

**Tests:** `PYTHONPATH=src python3 -m pytest -q tests/test_trace_context.py tests/test_trace_outcome_filter.py` — **32 passing** (15 new context cases, 17 earlier outcome cases; one Starlette deprecation warning from existing test machinery). `node --test tests/test_trace_investigation_ui.cjs` — **15 passing** (four new context behavior cases and 11 prior UI cases). New cases cover duplicate/competing task IDs, first/last event positions, absent IDs, bounds, malicious input, no payload-based identity inference, one-query preservation, lazy disclosure, context-only filtering with no extra API, reset and unsupported/fabricated context assertions.

**Limitations:** no authenticated machine/agent identity relationship is claimed or created; future provenance must derive from separately verified node/worker registration and signed/frozen custody. No browser pixel/keyboard live acceptance or ~85k-record query plan was measured. The change is still **read-only and review-only**, stacked on PRs #121/#122. The production collector, NAS, existing graph, service, agent authority and provider admission remain unchanged.

**Decision / next gate:** keep this context slice separate; validate accessibility and interactions in an authenticated real browser, then design truly trusted node/agent cross-navigation only after source identity metadata is verified. The independent historical full-fidelity audit requirement (August 8, 2025) remains open in auto-assist #117. This investigative router-event index is not evidence of complete raw tool-call retention.
