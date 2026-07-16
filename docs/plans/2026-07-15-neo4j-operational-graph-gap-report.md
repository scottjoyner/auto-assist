# Neo4j Operational Graph Gap Report

Date: 2026-07-15
Scope: Sophia voice-agent, AssistX/auto-assist, auto-router, auto-assign, auto-ingest, live Neo4j graph.

## Objective

Use Neo4j as the shared operational memory/control-plane for the stack, not just passive history. Every meaningful operation should carry one `correlation_id` through:

`voice/auth -> dispatch -> task -> route -> assignment -> execution -> completion -> evidence/context links`

Redis, SQLite, queues, and service-local caches may remain transport/read-model layers. The durable source of truth for trace, task, route, assignment, worker, context, and evidence state should be the graph.

## Live graph audit summary

Database `assistx` already has useful operational traces, but the trace graph is incomplete.

Observed counts from live audit:

- `EventEnvelope` top events:
  - `router.route_decision`: 36,761
  - `router.execution_stage.failed`: 24,021
  - `model.endpoint.discovered`: 13,810
  - `router.execution_stage.completed`: 12,601
  - `assignment.claimed`: 1,281
  - `assignment.heartbeat`: 1,096
  - `assignment.completed`: 1,038
  - `assignment.failed`: 220
- `TraceEvent` nodes are sparse:
  - `router.route_decision`: 9
  - `dispatch.requested`: 6
  - `voice.auth.accepted`: 3
  - `dispatch.accepted`: 1
- `Task` nodes in `assistx`: 30 observed in task status sample.
- All sampled `Task` nodes lacked direct TraceGroup/TraceEvent linkage in the audit query (`tasks_without_trace`: 30).
- `EventEnvelope` correlation coverage is low: 90,864 total EventEnvelope nodes, 3,649 with `correlation_id`.
- `TraceGroup` nodes exist but only have `correlation_id`, `created_at`, and `created_at_ts`; `current_state` is derived by API, not persisted.

Database `neo4j` contains the voice and broader knowledge graph:

- `Speaker`: 245,101
- `GlobalSpeaker`: 24,613
- `VoiceIdentity`: 2
- `VoiceprintVersion`: 2
- Speaker embedding gap remains the key voice issue:
  - `Speaker` nodes: 245,101 total, 1 with `embedding`
  - `GlobalSpeaker` nodes: 24,613 total, 24,587 with `embedding`
- Voice linkage exists but is uneven:
  - `Segment` -[:SPOKEN_BY]-> `Speaker`: 1,119,670
  - `Utterance` -[:SPOKEN_BY]-> `Speaker`: 1,094,782
  - `Speaker` -[:SAME_PERSON]-> `GlobalSpeaker`: 29,767
  - `VoiceIdentity` links exist to `VoiceprintGroup`, `VoiceprintVersion`, `Speaker`, and `GlobalSpeaker` only for a small enrolled set.

## Current code path map

### AssistX / auto-assist

Important files:

- `/home/scott/git/auto-assist/src/assistx/api.py`
- `/home/scott/git/auto-assist/src/assistx/swarm_core.py`
- `/home/scott/git/auto-assist/src/assistx/swarm_routes.py`
- `/home/scott/git/auto-assist/src/assistx/neo4j_client.py`

Implemented pieces:

1. `/api/voice/events` accepts Sophia voice events.
   - File: `src/assistx/api.py`, around lines 2065-2286.
   - Creates a `SignalEvent`.
   - Generates/extracts `correlation_id` via `metadata.correlation_id` or `corr:{event_id}`.
   - Records canonical `TraceEvent` for voice/dispatch using `record_trace_event`.
   - Returns `correlation_id` and `trace_url`.

2. `record_trace_event` creates `TraceEvent` and `TraceGroup`.
   - File: `src/assistx/swarm_core.py`, around lines 999-1052.
   - Writes `TraceEvent {event_id, correlation_id, event_type, source, task_id, dispatch_id, route_id, assignment_id, payload_json, ts, ts_ms}`.
   - Links `(:TraceGroup {correlation_id})-[:HAS_EVENT]->(:TraceEvent)`.

3. Trace read API exists.
   - File: `src/assistx/swarm_routes.py`, around lines 308-329.
   - `GET /api/traces/{correlation_id}` returns derived state and summary.
   - `POST /api/traces/{correlation_id}/events` can append explicit trace events.

4. EventEnvelope ingestion materializes some lifecycle state.
   - File: `src/assistx/swarm_core.py`.
   - `router.route_decision` and `route.selected` call `record_route_decision_trace`.
   - `assignment.*` updates task assignment/lease/completion state.

Gaps:

- `EventEnvelope` to `TraceEvent` projection is partial. Most `EventEnvelope` nodes with route/assignment lifecycle do not have corresponding `TraceEvent` nodes.
- `Task` nodes are updated by assignment events but are not directly linked into the `TraceGroup`/`TraceEvent` graph.
- `TraceGroup.current_state` is not persisted; state is API-derived. That is acceptable for reads but weak for graph-native dashboards/queries.
- Canonical trace type vocabulary is mixed: `router.route_decision` is used instead of only `route.selected` / `route.failed` / `route.blocked`.

### Sophia voice-agent

Important files:

- `/home/scott/git/Sophia/voice-agent/tests/test_dispatch_bridge_auto_config.py`
- `/home/scott/git/Sophia/voice-agent/tests/test_task_outbox.py`
- `/home/scott/git/Sophia/voice-agent/scripts/voice_insight.py`
- `/home/scott/git/Sophia/voice-agent/scripts/backfill_global_speaker_embeddings.py`

Implemented pieces:

1. Sophia dispatch bridge posts to AssistX `/api/voice/events`.
   - Tests assert target URL and dispatch status behavior.
   - Test references show the configured target endpoint is `http://assistant.local:8000/api/voice/events`.

2. Sophia task outbox tracks `event_id`, `correlation_id`, task title, response, and `task_id`.

3. Voice identity graph utilities exist.
   - `voice_insight.py` creates/updates `VoiceIdentity`, `VoiceSpeakerCluster`, training samples, and linkage.
   - `backfill_global_speaker_embeddings.py` exists to repopulate global speaker embedding/linkage.

Gaps:

- Sophia can send `correlation_id`, but downstream status should consistently read from AssistX trace API or directly from Neo4j rather than local bridge/outbox inference.
- Voice auth events should attach graph-native identity references in trace payloads: `voice_identity_id`, `speaker_id`, `global_speaker_id`, `device_id`, score, threshold, and auth decision.
- Speaker/local-global linkage is good for `GlobalSpeaker` embeddings but still weak for raw `Speaker` embedding coverage.

### auto-router

Important files:

- `/home/scott/git/auto-router/src/auto_router/assistx_routes.py`
- `/home/scott/git/auto-router/src/auto_router/event_dispatcher.py`
- `/home/scott/git/auto-router/src/auto_router/models.py`
- `/home/scott/git/auto-router/src/auto_router/settings.py`

Implemented pieces:

1. AssistX route endpoint exists.
   - File: `src/auto_router/assistx_routes.py`, around lines 245-309.
   - Endpoint: `POST /api/routes/request`.
   - Accepts `RouteRequest` with `correlation_id`, `dispatch_id`, `task_id`, intent, tools, metadata.
   - Selects lane/provider/model/target.
   - Enqueues `OutboxEvent` with `event_type=router.route_decision` and payload containing `correlation_id`, `route_id`, `task_id`, lane, provider, model, target info, rationale, confidence.

2. Event dispatcher wraps events for AssistX ingestion.
   - File: `src/auto_router/event_dispatcher.py`.
   - Extracts `correlation_id`, `task_id`, `route_id` from payload and includes them as envelope links.

Gaps:

- The router emits `router.route_decision`; graph consumers want canonical route outcome events (`route.selected`, `route.failed`, `route.blocked`) or a reliable mapping.
- `dispatch_id` is in `RouteRequest` but not included in the route decision payload written by `_build_route_decision`; this weakens full trace joins.
- The outbox must be checked for delivery pressure; live graph has many route EventEnvelopes but very few TraceEvents, implying projection/delivery/materialization is incomplete.

### auto-assign

Important files:

- `/home/scott/git/auto-assign/src/auto_assign/service.py`
- `/home/scott/git/auto-assign/src/auto_assign/main.py`
- `/home/scott/git/auto-assign/src/auto_assign/events.py`
- `/home/scott/git/auto-assign/src/auto_assign/models.py`

Implemented pieces:

1. Event taxonomy exists.
   - File: `src/auto_assign/events.py`, around lines 16-20.
   - Includes `assignment.requested`, `assignment.claimed`, `assignment.heartbeat`, `assignment.completed`, `assignment.failed`.

2. Claim lifecycle is implemented.
   - File: `src/auto_assign/service.py`, around lines 507-555.
   - `claim_assignment` updates local SQLite mirror, creates `EventEnvelope(event_type=assignment.claimed)`, includes `correlation_id`, `assignment_id`, `task_id`, `worker_id`, `node_id`, lease data, route_id, capabilities, metadata, and enqueues to outbox.

3. Completion lifecycle is implemented.
   - File: `src/auto_assign/service.py`, around lines 557-594.
   - `complete_assignment` creates `assignment.completed` or `assignment.failed`, includes `correlation_id`, assignment/task/worker/status/summary/artifacts, and enqueues to outbox.

4. Heartbeat lifecycle is implemented.
   - File: `src/auto_assign/service.py`, around lines 596-640.
   - `record_heartbeat_with_lease_renewal` emits `assignment.heartbeat`, includes `correlation_id`, node/worker/assignment state, and lease renewal details.

5. Outbox dispatch/reconciliation exists.
   - File: `src/auto_assign/service.py`, around lines 390-447.
   - Checks AssistX event status and posts pending events.
   - Local cache is explicitly marked as non-canonical / mirror with `canonical_source=neo4j_via_assistx` in API responses.

Gaps:

- auto-assign local cache/read endpoints are not graph-native; they report SQLite mirror state and expect AssistX/Neo4j to be canonical.
- There is no direct graph query endpoint that asks Neo4j for stale assignments/heartbeats by trace, node, or worker.
- Assignment events appear numerous as `EventEnvelope`, but corresponding `TraceEvent` projection is missing or sparse.
- Need durable `Assignment` nodes or direct relationships such as `(:Task)-[:HAS_ASSIGNMENT]->(:Assignment)` and `(:Assignment)-[:CLAIMED_BY]->(:Worker|:SwarmNode)`; current graph appears mostly task properties and EventEnvelopes.

### auto-ingest

Important files:

- `/home/scott/git/auto-ingest/summarize_from_segments.py`
- `/home/scott/git/auto-ingest/link_global_speakers.py`
- `/home/scott/git/auto-ingest/link_global_speakers_2.py`
- `/home/scott/git/auto-ingest/scripts/extract_neo4j_schema.py`
- `/home/scott/git/auto-ingest/postprocess_audiov4.py`

Implemented pieces:

1. Ingest writes large volumes of transcript/audio/segment/speaker graph data into database `neo4j`.
2. `summarize_from_segments.py` can create `Summary` and `Task` nodes from transcriptions and link `(:Summary)-[:GENERATED_TASK]->(:Task)`.
3. Global speaker linking scripts exist and can link local `Speaker` nodes to `GlobalSpeaker` via `SAME_PERSON`.
4. Schema extraction script is aware of `Task`, `PhoneLog`, `DashcamClip`, `VaultDocument`.

Gaps:

- auto-ingest is not yet emitting operational `EventEnvelope` / `TraceEvent` records like `ingest.evidence.linked` or `context.available` into AssistX trace graph.
- Ingest-created `Task` nodes in database `neo4j` are not obviously synchronized with AssistX `Task` nodes in database `assistx`.
- Evidence/context artifacts are not represented as a canonical `ContextPacket` / `Evidence` layer linked to operational tasks by `correlation_id`.

## Primary architecture gaps

1. Trace projection gap

`EventEnvelope` is much richer than `TraceEvent`. The live graph has tens of thousands of route/assignment EventEnvelopes but only 19 TraceEvents in the sampled count. This prevents direct trace timeline queries.

Patch direction:

- In AssistX `record_event`, project every lifecycle EventEnvelope into `TraceEvent` when `correlation_id` exists.
- Event types to project first:
  - `dispatch.requested`
  - `dispatch.accepted`
  - `router.route_decision`
  - `route.selected`
  - `route.failed`
  - `route.blocked`
  - `assignment.requested`
  - `assignment.recommended`
  - `assignment.claimed`
  - `assignment.heartbeat`
  - `assignment.completed`
  - `assignment.failed`
  - `assignment.expired`
  - `ingest.evidence.linked`
  - `context.available`

2. Task/trace linkage gap

Tasks exist, TraceGroups exist, but task-to-trace relationships are sparse/missing.

Patch direction:

- Extend `record_trace_event` to link:
  - `(:TraceEvent)-[:ABOUT_TASK]->(:Task)` when `task_id` exists.
  - `(:TraceGroup)-[:TRACES_TASK]->(:Task)` when `task_id` exists.
  - `(:TraceEvent)-[:ABOUT_DISPATCH]->(:Dispatch)` when `dispatch_id` exists.
  - `(:TraceEvent)-[:ABOUT_ASSIGNMENT]->(:Assignment)` when `assignment_id` exists.
  - `(:TraceEvent)-[:ABOUT_ROUTE]->(:RouteDecision)` when `route_id` exists.

3. Missing explicit route/assignment nodes

Route and assignment details mostly live as event payload/properties, not as durable graph entities.

Patch direction:

- Materialize `RouteDecision {route_id}` nodes from route events.
- Materialize `Assignment {assignment_id}` nodes from assignment events.
- Link:
  - `(:Task)-[:ROUTED_BY]->(:RouteDecision)`
  - `(:RouteDecision)-[:TARGETS_NODE]->(:SwarmNode)`
  - `(:RouteDecision)-[:TARGETS_SERVICE]->(:ServiceEndpoint)`
  - `(:Task)-[:HAS_ASSIGNMENT]->(:Assignment)`
  - `(:Assignment)-[:CLAIMED_BY]->(:Worker|:SwarmNode)`

4. Correlation propagation gap

Only about 4% of `EventEnvelope` nodes have `correlation_id` in the audit.

Patch direction:

- Require `correlation_id` for all new lifecycle events at service boundaries.
- Where old events lack it but have task/route/assignment IDs, derive/backfill correlation by connected event/task lineage.

5. Sophia graph-native status gap

Sophia returns/holds bridge and outbox state, but the true status should be trace-derived.

Patch direction:

- Sophia should store returned `correlation_id` and call AssistX `GET /api/traces/{correlation_id}` for dispatch/task status.
- For voice auth, include `voice_identity_id`, `speaker_id`, `global_speaker_id`, `device_id`, score/threshold in trace payload.

6. auto-ingest evidence/context gap

auto-ingest writes rich knowledge data but does not publish operational evidence/context availability events.

Patch direction:

- Add a small AssistX event sink client to auto-ingest.
- Emit `ingest.evidence.linked` when a transcript/audio/summary/document is linked to a task/correlation.
- Emit `context.available` when a context packet is ready for router/assigner use.
- Materialize `ContextPacket` / `Evidence` nodes or map existing `Summary`, `VaultDocument`, `Transcript`, `DashcamClip`, `PhoneLog` as evidence nodes linked to `Task` / `TraceGroup`.

## Recommended next patches, in order

### Patch 1: AssistX trace projection hardening

Repo: `/home/scott/git/auto-assist`

Change:

- In `src/assistx/swarm_core.py`, centralize projection from `EventEnvelope` to `TraceEvent` for all correlation-bearing lifecycle events.
- Extend `record_trace_event` to link tasks/routes/assignments/dispatches to TraceGroup and TraceEvent.
- Persist `TraceGroup.current_state` and `TraceGroup.updated_at_ts` whenever an event is appended.

Acceptance checks:

- Post or replay an `assignment.claimed` EventEnvelope with `correlation_id`.
- Verify:
  - `TraceEvent` count increases.
  - `GET /api/traces/{correlation_id}` includes the assignment event.
  - Cypher can find `(TraceGroup)-[:TRACES_TASK]->(Task)`.

### Patch 2: auto-router canonical route outcome

Repo: `/home/scott/git/auto-router`

Change:

- Include `dispatch_id` in `RouteDecision` payload.
- Either emit canonical `route.selected`/`route.blocked`/`route.failed` or add `canonical_event_type` while preserving existing `router.route_decision` for compatibility.

Acceptance checks:

- `POST /api/routes/request` returns payload with `dispatch_id` when supplied.
- Outbox event includes route ID, task ID, dispatch ID, correlation ID, and canonical route outcome.

### Patch 3: auto-assign graph read model / stale query

Repo: `/home/scott/git/auto-assign`

Change:

- Keep SQLite as local mirror, but add AssistX/Neo4j-backed read endpoint or AssistX proxy query for assignment state by `correlation_id`, `task_id`, and stale heartbeat.
- Ensure `assignment.expired` includes `correlation_id` when it can be recovered from the assignment row.

Acceptance checks:

- Claim/heartbeat/completion lifecycle produces both EventEnvelope and TraceEvent timeline.
- Stale assignment query can be answered from Neo4j without reading local SQLite.

### Patch 4: Sophia trace status readback

Repo: `/home/scott/git/Sophia`

Change:

- After `/api/voice/events` returns `correlation_id` and `trace_url`, store them in task outbox.
- Add/read path for `/dispatch/status` to include graph trace state from AssistX.

Acceptance checks:

- Voice-created task response shows correlation ID.
- Dispatch status can show `pending_route`, `pending_assignment`, `running`, `completed`, or `failed` from trace events.

### Patch 5: auto-ingest evidence events

Repo: `/home/scott/git/auto-ingest`

Change:

- Add small event client that posts to AssistX `/api/events` or `/api/traces/{correlation_id}/events`.
- Emit `ingest.evidence.linked` and `context.available` for summaries/transcripts/documents tied to tasks/correlations.

Acceptance checks:

- Given a task/correlation and transcript summary, graph query can find:
  - `TraceGroup -> Task -> Evidence/Summary/Transcript`
  - latest `ingest.evidence.linked` TraceEvent

## Useful graph queries to add as dashboard/report checks

```cypher
// Traces with route decision but no assignment event
MATCH (g:TraceGroup)-[:HAS_EVENT]->(r:TraceEvent)
WHERE r.event_type IN ['router.route_decision','route.selected']
AND NOT EXISTS {
  MATCH (g)-[:HAS_EVENT]->(a:TraceEvent)
  WHERE a.event_type STARTS WITH 'assignment.'
}
RETURN g.correlation_id, r.task_id, r.route_id, r.ts_ms
ORDER BY r.ts_ms DESC
LIMIT 50;
```

```cypher
// Tasks with no trace linkage
MATCH (t:Task)
WHERE NOT EXISTS { MATCH (:TraceGroup)-[:TRACES_TASK]->(t) }
RETURN t.id, t.status, t.kind, t.title, t.created_at_ts
ORDER BY t.created_at_ts DESC
LIMIT 50;
```

```cypher
// Assignment events without projected trace events
MATCH (e:EventEnvelope)
WHERE e.event_type STARTS WITH 'assignment.' AND e.correlation_id IS NOT NULL
AND NOT EXISTS {
  MATCH (:TraceEvent {correlation_id:e.correlation_id, event_type:e.event_type})
}
RETURN e.event_type, e.correlation_id, e.task_id, e.assignment_id, e.created_at_ts
ORDER BY e.created_at_ts DESC
LIMIT 50;
```

```cypher
// Speaker embedding gap
MATCH (s:Speaker)
RETURN count(s) AS speakers,
       count(s.embedding) AS with_embedding,
       count { (s)-[:SAME_PERSON]->(:GlobalSpeaker) } AS linked_to_global;
```

## Bottom line

The stack already has most of the event emitters and enough graph schema to start. The highest-value fix is not more planning; it is closing the projection/linkage loop inside AssistX so every `EventEnvelope` with a `correlation_id` becomes a first-class trace timeline event and links to Task, RouteDecision, Assignment, Worker/SwarmNode, and Evidence where IDs are available.

Once that patch lands, Sophia and dashboards can stop guessing from local state and ask Neo4j: "what happened to this correlation?"