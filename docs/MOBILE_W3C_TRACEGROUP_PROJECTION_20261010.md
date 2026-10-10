# AssistX mobile W3C → TraceGroup adapter

**State: draft-only, observational, no persistent writes.** Base: [AssistX PR #270](https://github.com/scottjoyner/auto-assist/pull/270), which is stacked on catalog repair #254. iOS sender: [PR #383](https://github.com/scottjoyner/kipnerter-ios/pull/383), receipt UI: [PR #389](https://github.com/scottjoyner/kipnerter-ios/pull/389).

## Reason

AssistX already has a Neo4j graph `TraceEvent` / `TraceGroup` writer in `swarm_core.record_trace_event` with links to tasks, dispatches, assignments and routes. Creating another persistent trace database now would fork the schema and risk writing sensitive or untrusted client material to the graph. We first establish a **pure, read-only projection contract**.

## Canonical bridge

`project_mobile_model_trace` strictly checks the W3C v00 traceparent and the actual `kmr:` + 32 lowercase hex model-request ID emitted by AssistX. Only six fixed stage types are projected: `gateway_accepted`, `router_http_accepted`, `json_response_validated`, `sse_stream_opened`, `sse_stream_finished`, `router_failed`. Stages deliberately distinguish HTTP 200 and an opened SSE stream from a completed generation.

It emits only `record_trace_event`-compatible kwargs:
- `correlation_id` is the **server-issued** `mobile-model:kmr:<32-hex>` namespace, not the client-controlled W3C trace ID. This isolates the mobile request TraceGroup from existing task and agent groups even when an authenticated client chooses a colliding trace ID.
- The validated W3C 128-bit `trace_id` remains an observational payload property for later authorized cross-device search. Multiple stages with the same server request ID join one graph group; attempts still retain their own child span IDs.
- `event_type` is one of the six whitelisted `mobile.model.*` events.
- `source` is the fixed string `assistx.mobile.model.gateway`.
- `payload` contains the fixed schema version, trace ID, parent span ID, canonical server-generated request ID, boundary label, `attestation_level=observational-only`, and optional bounded gateway status 200–599.

No model names, hostnames, request bodies, streamed response content, user identities, tokens, headers, credentials, network URLs, cost accounting or arbitrary user data are projected.

## Persistence and security gate

**There is NO call to `record_trace_event` here and no live Neo4j write.** A later separately approved integration must keep the mobile-auth, Tailnet and signed projection admission barriers ahead of event recording, apply rate/retention limits, avoid passing arbitrary client fields to Cypher, and validate storage capacity/performance on a disposable Neo4j graph before introducing persistence.

Do not treat a W3C trace ID as authentication, a gateway HTTP response as runtime approval, or an echo as backend acknowledgment beyond the receiving HTTP boundary.

## Evidence

`PYTHONPATH=src python3 -m pytest -q tests/test_mobile_trace_projection.py tests/test_mobile_trace_context.py tests/test_mobile_agent_routes.py tests/test_mobile_runtime_catalog.py` passed **31 tests** on a clean isolated x1-370 worktree. New projection tests cover canonical graph kwargs, request-ID rejection, invalid W3C values, whitelisted stage semantics, invalid status types and shared trace ID across separate child spans.

The remaining steps are disposable graph experiment, opt-in bounded persistence, authenticated read-only retrieval and physical iPhone correlation proof against a signed build. Maintain fail-closed Agent Auto admission independently.

## Disposable graph integration experiment — observed 2026-10-10

We executed the *real* `swarm_core.record_trace_event` function against `assistx.contracts.tests.fake_neo4j.FakeNeo4j(apoc=False)` using canonical W3C mobile projection kwargs. Assertions prove: a projected HTTP-accepted receipt creates a `(:TraceEvent)`, links to `(:TraceGroup {correlation_id:trace_id})`, stores only the whitelisted payload, and makes **no** `FOR_TASK`, `FOR_ROUTE` or `FOR_ASSIGNMENT` authority links. A three-stage observation sequence writes three events under the same trace group, without merging child span identity or gaining runtime authority.

The combined contract suite `PYTHONPATH=src python3 -m pytest -q tests/test_mobile_trace_projection.py src/assistx/contracts/tests/test_trace_linkage.py tests/test_mobile_trace_context.py tests/test_mobile_agent_routes.py tests/test_mobile_runtime_catalog.py` passed **39 tests**. This is a fake graph, not physical Neo4j. A production graph migration, admission integration and durable write/retention acceptance are still blocked and not claimed.

## Spoofing/collision hardening

A malicious or misconfigured client can set `traceparent` to the ID of an unrelated task's graph trace. Keying `TraceGroup.correlation_id` by that untrusted header could merge mobile events into the other group's state. The projector now binds grouping to the **server-issued** `kmr` request ID, prefixed with `mobile-model:`; this value must come from AssistX's own generated request ID after authentication and model-handle validation, never from client JSON or headers. The W3C ID stays in the whitelist payload. A dedicated regression proves a chosen client W3C value cannot equal the mobile TraceGroup key. This still does not provide cryptographic attestation or permission to persist into live Neo4j.
