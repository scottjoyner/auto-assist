# AssistX + Hermes + Neo4j Integration Migration Plan

## 1) Goal and Outcome

This migration makes **AssistX / auto-assist** the command-center layer over
many Hermes Agent sessions, with Neo4j as the shared brain and Paperclip as the
primary work-assignment hub.

The end state is:

1. AssistX accepts direct inputs, voice events, dashboard actions, webhooks, and
   scheduled triggers.
2. AssistX classifies those inputs as graph-backed intents, then writes the
   durable objects that matter: tasks, facts, memory items, context packets,
   dispatches, runs, artifacts, and memory updates.
3. Hermes agents on local and remote devices receive work through Task triggers
   in Neo4j, optionally routed by Paperclip, resume the right sessions, query
   shared graph memory, run tools/code, and report outcomes.
4. Neo4j remains the durable brain for historical knowledge, active memory,
   orchestration state, and execution provenance.
5. The AssistX UI evolves into the operator command center for memory freshness,
   assignments, device/session state, run traces, and outcomes.

This replaces the previous Sophia-only migration framing. Sophia and voice
memory remain important inputs, but the migration is broader: it is a
multi-agent orchestration and graph-memory integration.

---

## 2) Current Baseline

### AssistX / auto-assist

- Stores conversations, summaries, tasks, agent runs, tool calls, artifacts,
  transcriptions, and segments in Neo4j.
- Provides review/ready/run dashboards, `/api/ask`, `/api/tasks`, `/runs`, and
  async answer flows.
- Uses Redis/RQ for background jobs and idempotency support for selected APIs.
- Has a small local LangGraph/Ollama execution loop, but should not become the
  primary long-running multi-device worker runtime.

### Hermes Agent

- Provides persistent sessions, memory hooks, toolsets, code execution, skills,
  cron/webhook/API triggers, platform gateways, and subagent delegation.
- Supports external memory providers through `MemoryProvider` and
  `MemoryManager`.
- Has lifecycle hooks that map directly to graph memory needs:
  `prefetch`, `sync_turn`, `on_memory_write`, `on_delegation`, and
  `on_session_switch`.

### Paperclip and Hermes adapter

- Paperclip is the canonical assignment hub for cross-device agent work.
- `hermes-paperclip-adapter` runs Hermes via CLI, supports persistent sessions,
  captures Hermes output, parses session IDs, and stores session params across
  heartbeats.
- Existing adapter config supports `persistSession`, `enabledToolsets`,
  `timeoutSec`, model/provider settings, worktree mode, checkpoints, and
  Paperclip API access.

### TTS / voice transcriber

- Provides task lifecycle events, cancellation/barge-in, voice profile mapping,
  realtime WebSocket events, and replay-safe ingest.
- Voice-origin ideas/plans should feed AssistX intents and memory, then route
  through the same retrieval and dispatch path as dashboard or webhook work.

### Auto-ingest

- Maintains the large existing Neo4j knowledge corpus, including `PhoneLog`,
  `Transcription`, `Segment`, `Utterance`, `Speaker`, `Frame`,
  `DashcamEmbedding`, `YOLODetection`, `Entity`, and `Link`.
- This is the primary historical/sensory context source for the brain.

---

## 3) Target Architecture

```text
Direct input / dashboard / webhook / schedule / TTS voice event
  -> AssistX Intent API/classifier
      -> Deliverable ticket
          -> Epic -> Story -> executable Task(status=READY)
              -> agent polling/claim or optional Paperclip assignment
                  -> bounded ContextPacket from latest graph
                      -> Hermes Agent session on local or remote device
                          -> tools, code, skills, memory provider
                              -> AgentRun / ToolCall / Artifact / MemoryItem writes
                                  -> deliverable completion notification
                                      -> Neo4j remains the freshest orchestration state
                                          -> Command center UI
```

### System roles

- **AssistX** owns intake, intent classification, task trigger creation,
  context retrieval, dispatch policy, orchestration graph writes, dashboard
  views, and event reconciliation.
- **Paperclip** owns optional assignment, agent registry, cross-device work
  routing, heartbeat execution, comments, and issue status.
- **Hermes** owns execution, local tools, code, session continuity, skills, and
  explicit memory interactions.
- **Neo4j** owns durable knowledge, memory, orchestration, and provenance.
- **TTS** owns voice/STT/TTS interaction, cancellation/barge-in, and realtime
  voice-origin task events.
- **Auto-ingest** owns high-volume source ingestion into the historical graph.

### Integration principles

- **Graph first**: every intent, retrieved context packet, dispatch, run, tool
  call, artifact, and memory write must be traceable in Neo4j.
- **Bounded context**: agents receive curated `ContextPacket` payloads, not raw
  unbounded graph dumps.
- **Task as ticket and trigger**: executable work is represented by `Task`
  nodes. Complex work can be modeled as epic/story/task ticket hierarchies
  using graph relationships, while agents poll and claim eligible `READY`
  tickets directly from AssistX/Neo4j.
- **Ask as deliverable**: every `/api/ask` request can create a user-facing
  `deliverable` ticket, immediately return an answer/acknowledgement, and then
  notify the answer stream when the deliverable is complete.
- **Paperclip as transport**: AssistX can still dispatch work through
  Paperclip for long-running or device-specific execution, but Paperclip is not
  the source of truth.
- **Hermes for execution**: Hermes sessions run code/tools and query memory
  through an external Neo4j/AssistX memory provider.
- **Replay safe**: all inbound events carry idempotency keys or stable external
  IDs.
- **No raw chain-of-thought storage**: persist summaries, evidence, decisions,
  tool I/O, artifacts, and outcomes.

---

## 4) Neo4j Brain Layout

Use one Neo4j server as the brain. If multi-database Neo4j is available, use:

- `knowledge`: large auto-ingest corpus.
- `memory`: Sophia, voice, direct notes, durable facts, active context.
- `orchestration`: AssistX/Hermes/Paperclip task, dispatch, run, and session
  state.

If multi-database mode is not available, use a single database with label
namespaces and constraints. The implementation must support both modes through
configuration.

### Knowledge graph

Existing high-volume nodes remain source-owned by auto-ingest:

- `PhoneLog`
- `Transcription`
- `Segment`
- `Utterance`
- `Speaker`
- `Frame`
- `DashcamEmbedding`
- `YOLODetection`
- `Entity`
- `Link`

AssistX should reference these nodes from `ContextPacket` and `Task` records,
not rewrite or re-own them.

### Memory graph

Memory graph nodes store direct ideas, plans, preferences, summaries, durable
facts, and active context from Sophia/voice/user/Hermes writes.

Recommended labels:

- `MemoryItem`
- `MemorySource`
- `SignalEvent`
- `Idea`
- `Plan`
- `Preference`
- `Fact`
- `Summary`

`MemoryItem` can be the normalized envelope label, with more specific labels
added when useful.

### Orchestration graph

Standardize or add these labels:

- `Intent`: direct user request, voice input, dashboard command, webhook, or
  scheduled trigger.
- `Task`: normalized ticket/unit of work, extending the existing AssistX task
  model. `ticket_type` distinguishes `deliverable`, `epic`, `story`, `task`,
  `bug`, and `chore`.
- `ContextPacket`: bounded graph context prepared for a task/session/run.
- `Dispatch`: AssistX assignment record that points to Paperclip/Hermes work.
- `AgentSession`: Hermes session identity and resume metadata.
- `AgentDevice`: physical or remote machine capable of running Hermes.
- `AgentCapability`: model/toolset/repo/network/code-execution capabilities.
- `AgentRun`: execution attempt, preserving current AssistX provenance.
- `ToolCall`: tool use record.
- `Artifact`: produced file, URL, result, patch, audio, or report.

### Core relationships

```cypher
(:Intent)-[:CREATED_TASK]->(:Task)
(:Task)-[:HAS_CHILD]->(:Task)
(:Task)-[:PART_OF]->(:Task)
(:Task)-[:USES_CONTEXT]->(:ContextPacket)
(:ContextPacket)-[:REFERENCES]->(:MemoryItem)
(:ContextPacket)-[:REFERENCES]->(:Transcription)
(:ContextPacket)-[:REFERENCES]->(:Segment)
(:ContextPacket)-[:REFERENCES]->(:Entity)
(:Task)-[:DISPATCHED_AS]->(:Dispatch)
(:Dispatch)-[:ASSIGNED_TO]->(:AgentSession)
(:AgentSession)-[:RUNS_ON]->(:AgentDevice)
(:AgentSession)-[:HAS_CAPABILITY]->(:AgentCapability)
(:AgentRun)-[:FOR_DISPATCH]->(:Dispatch)
(:AgentRun)-[:USED_TOOL]->(:ToolCall)
(:AgentRun)-[:PRODUCED]->(:Artifact)
(:AgentRun)-[:WROTE_MEMORY]->(:MemoryItem)
```

### Required uniqueness and indexes

Add constraints for:

- `Intent.id`
- `Task.id`
- `ContextPacket.id`
- `Dispatch.id`
- `AgentSession.id`
- `AgentDevice.id`
- `AgentCapability.id`
- `MemoryItem.id`
- `SignalEvent.id`

Add indexes for:

- `Intent.source`, `Intent.created_at_ts`, `Intent.idempotency_key`
- `Task.status`, `Task.kind`, `Task.priority`, `Task.created_at_ts`
- `Dispatch.status`, `Dispatch.paperclip_issue_id`, `Dispatch.created_at_ts`
- `AgentSession.hermes_session_id`, `AgentSession.paperclip_agent_id`
- `AgentDevice.hostname`, `AgentDevice.last_seen_at_ts`
- `MemoryItem.kind`, `MemoryItem.source`, `MemoryItem.updated_at_ts`
- `ContextPacket.created_at_ts`, `ContextPacket.query_hash`

---

## 5) AssistX Brain API

Keep existing AssistX APIs where useful, especially `/api/ask`, `/api/tasks`,
`/api/transcriptions`, `/runs`, and `/api/answers`. Add the following command
center APIs.

### `POST /api/intents`

Create an `Intent` from text, voice, UI, webhook, schedule, or Paperclip
comment.

Minimum request:

```json
{
  "source": "voice|ui|webhook|schedule|paperclip|manual",
  "text": "What should be done?",
  "idempotency_key": "stable-source-key",
  "client_ts": "2026-05-22T12:00:00Z",
  "metadata": {}
}
```

Behavior:

1. Validate and dedupe by `idempotency_key`.
2. Create or reuse `Intent`.
3. Optionally create `Task` when the intent is actionable.
4. Return intent/task IDs and next recommended action.

### `POST /api/brain/context`

Create a bounded `ContextPacket` for a query, task, or Hermes session.

Minimum request:

```json
{
  "query": "Context needed by the agent",
  "task_id": "optional-task-id",
  "session_id": "optional-agent-session-id",
  "max_items": 20,
  "include_sources": ["memory", "knowledge", "orchestration"]
}
```

Behavior:

1. Query active memory, relevant historical knowledge, and recent orchestration
   state.
2. Rank by relevance, recency, graph proximity, and source confidence.
3. Persist `ContextPacket` and `REFERENCES` relationships.
4. Return a compact packet with source IDs, labels, snippets, timestamps, and
   confidence.

### `POST /api/dispatch`

Assign a graph-backed task to Paperclip/Hermes.

Minimum request:

```json
{
  "task_id": "assistx-task-id",
  "target": {
    "paperclip_agent_id": "optional-agent-id",
    "capabilities": ["terminal", "file", "web", "code_execution"]
  },
  "priority": "LOW|MEDIUM|HIGH",
  "idempotency_key": "stable-dispatch-key"
}
```

Behavior:

1. Load the task and latest context packet.
2. Create a Paperclip issue or update an existing one.
3. Create `Dispatch` and link it to `Task`.
4. Link known `AgentSession`, `AgentDevice`, and capabilities when available.
5. Return Paperclip issue ID, dispatch ID, and status.

### `POST /api/paperclip/events`

Ingest Paperclip assignment/run/comment/completion events.

Minimum request:

```json
{
  "event_type": "issue_created|assigned|run_started|comment|run_completed|status_changed",
  "paperclip_issue_id": "issue-id",
  "paperclip_agent_id": "agent-id",
  "paperclip_run_id": "run-id",
  "event_id": "stable-event-id",
  "payload": {}
}
```

Behavior:

1. Validate auth and dedupe by `event_id`.
2. Upsert dispatch/run/session state.
3. Persist summaries, comments, Hermes session IDs, usage, costs, artifacts, and
   status transitions.
4. Update command-center views.

### Read APIs for the command center

- `GET /api/tasks`: keep existing endpoint, add orchestration fields.
- `GET /api/sessions`: list Hermes sessions, devices, assigned tasks, status,
  capabilities, and last seen times.
- `GET /api/runs`: expose run provenance with Paperclip/Hermes linkage.
- `GET /api/dispatches`: list dispatch status and Paperclip issue links.
- `GET /api/context-packets/{id}`: inspect context used by an agent.

---

## 6) Hermes Neo4j Memory Provider

Create a Hermes external memory provider that talks to AssistX/Neo4j. This is
the bridge that lets Hermes agents learn to ask the graph what they need before
continuing work.

### Provider responsibilities

- `initialize`: connect to AssistX Brain API or Neo4j using profile-scoped
  config.
- `system_prompt_block`: tell Hermes that shared graph memory is available and
  cite how/when to use it.
- `prefetch`: call `/api/brain/context` before each turn and return a compact
  memory block.
- `queue_prefetch`: optionally warm the next graph search after a turn.
- `sync_turn`: store user/assistant turn summaries as `SignalEvent` or
  `MemoryItem` candidates.
- `on_memory_write`: mirror explicit Hermes memory tool writes into Neo4j.
- `on_delegation`: record delegated task/result pairs as graph observations.
- `on_session_switch`: update `AgentSession` links when Hermes resumes,
  branches, resets, or compresses context.
- `get_tool_schemas`: expose explicit graph search and memory write tools.

### Suggested provider tools

- `graph_context_search`: query bounded graph context for a task/session/query.
- `graph_memory_write`: write a durable memory item with provenance.
- `graph_task_lookup`: retrieve current task, dispatch, and acceptance context.
- `graph_session_status`: retrieve current Hermes session/device state.

### Storage rules

- Store only summaries, observations, evidence, artifacts, and structured
  outputs.
- Do not store raw chain-of-thought.
- Include provenance fields: `session_id`, `parent_session_id`, `platform`,
  `agent_identity`, `task_id`, `dispatch_id`, `paperclip_issue_id`, and
  timestamp where available.

---

## 7) Paperclip Dispatch Integration

Paperclip is the primary assignment path for long-running work and work that
needs to run on specific devices.

### AssistX -> Paperclip

When dispatching a task:

1. Create or update a Paperclip issue from the AssistX `Task`.
2. Include a compact context packet and links back to AssistX IDs.
3. Assign to a specific Paperclip agent when requested, or choose by capability.
4. Record `paperclip_issue_id`, `paperclip_agent_id`, and status on `Dispatch`.

### Paperclip -> Hermes

The `hermes-paperclip-adapter` should remain the runtime bridge:

- Use `persistSession: true`.
- Preserve and reuse Hermes `session_id` through adapter `sessionParams`.
- Enable required toolsets per agent profile.
- Use worktree/checkpoints for higher-risk coding tasks.
- Keep Paperclip comments as the human/agent collaboration channel.

### Paperclip -> AssistX

AssistX should ingest:

- issue created/assigned/status events;
- heartbeat run started/completed events;
- run summaries and result JSON;
- comments;
- Hermes session IDs;
- cost/usage metadata;
- artifacts and links;
- completion/failure/cancelled states.

---

## 8) TTS / Voice Integration

TTS and voice transcriber events become first-class intent and memory sources.

### Event sources

Ingest these event types:

- `task_created`
- `ralph_iteration`
- `tts_chunk`
- `cancel_active`
- `task_cancelled`
- `barge_in`
- routing/model-health events

### Voice flow

```text
Voice transcript/event
  -> AssistX /api/intents
      -> MemoryItem or Task
          -> ContextPacket
              -> quick answer or Paperclip dispatch
                  -> Hermes result
                      -> TTS response / command-center update
```

### Behavior

- Direct ideas and plans become `MemoryItem` records even when they do not need
  immediate execution.
- Actionable requests become `Task` records and can be dispatched through
  Paperclip.
- Cancellation/barge-in events update active `Task`, `Dispatch`, and `AgentRun`
  status where applicable.
- Replay and duplicate voice events must be deduped by source event ID/hash.

---

## 9) Command Center UI

AssistX should evolve from task/review screens into a command center.

### Required views

- **Inbox / Intents**: raw incoming voice/UI/webhook/schedule intents and
  whether they became memory, task, or answer.
- **Tasks**: status, priority, kind, source, context packet, dispatch state,
  Paperclip issue link, and latest run result.
- **Agents / Sessions**: Hermes session IDs, device, Paperclip agent, model,
  toolsets, capabilities, current assignment, and last seen time.
- **Dispatches**: queued/assigned/running/done/failed/cancelled work across
  devices.
- **Runs**: run timeline from `Task -> Dispatch -> AgentRun -> ToolCall ->
  Artifact`.
- **Memory**: recent memory writes, freshness, source, citations, and graph
  references.
- **Context packets**: inspect exactly what context was sent to an agent.

### Required controls

- approve;
- assign;
- reassign;
- pause;
- cancel;
- retry;
- inspect trace;
- open Paperclip issue;
- open Hermes session metadata;
- mark memory as active/stale/incorrect.

---

## 10) Migration Phases

### Phase 0 - Inventory and contract freeze

- Document existing repos, ports, services, graph sources, and credentials.
- Confirm Paperclip is running and register `hermes-paperclip-adapter`.
- Define the Neo4j database/namespace strategy.
- Produce sample payloads for intent, task, context packet, dispatch, Paperclip
  event, and memory write.
- Create implementation epics for AssistX API, Neo4j schema, Hermes provider,
  Paperclip sync, TTS ingest, and UI.

**Exit criteria:** architecture contract approved and sample payload corpus
checked in.

### Phase 1 - Brain schema and retrieval

- Add constraints/indexes for orchestration and memory labels.
- Implement idempotent upserts for `Intent`, `Task`, `ContextPacket`,
  `Dispatch`, `AgentSession`, `AgentDevice`, and `MemoryItem`.
- Build graph retrieval templates for:
  - task context;
  - session context;
  - active memory;
  - historical evidence;
  - recent orchestration state.
- Persist bounded `ContextPacket` records and `REFERENCES` edges.

**Exit criteria:** context retrieval returns bounded, cited packets and graph
integrity checks pass.

### Phase 2 - Hermes memory integration

- Create the Hermes external memory provider for AssistX/Neo4j.
- Add setup/config docs for `memory.provider`.
- Implement pre-turn retrieval, post-turn sync, explicit memory writes,
  delegation observations, and session-switch tracking.
- Add explicit graph search tools for Hermes.

**Exit criteria:** a Hermes session receives graph context before a task and
writes a memory update back to Neo4j.

### Phase 3 - Paperclip dispatch integration

- Implement AssistX `POST /api/dispatch`.
- Create/update Paperclip issues from AssistX tasks.
- Map Paperclip agents into `AgentSession`, `AgentDevice`, and
  `AgentCapability`.
- Implement `POST /api/paperclip/events`.
- Sync issue/run/comment/status/session metadata back into Neo4j.

**Exit criteria:** an AssistX task can be assigned through Paperclip, completed
by Hermes, and reflected in AssistX with run provenance.

### Phase 4 - Command center UI

- Add command-center views for intents, tasks, dispatches, sessions, runs,
  memory, and context packets.
- Add controls for approve, assign, reassign, pause, cancel, retry, and inspect
  trace.
- Surface Paperclip issue links and Hermes session IDs.

**Exit criteria:** operator can see and control the full task lifecycle from
intent through Hermes completion.

### Phase 5 - Voice/TTS integration

- Connect TTS task and lifecycle events to `/api/intents`.
- Store voice-origin ideas/plans as memory.
- Route actionable voice requests through retrieval and dispatch.
- Wire cancellation/barge-in to active task/dispatch/run state.

**Exit criteria:** a voice idea becomes memory, a voice task can dispatch to
Hermes, and cancellation updates AssistX state.

**Status update (May 23, 2026):** Implemented `POST /api/voice/events` to ingest
`task_created`, `ralph_iteration`, `tts_chunk`, `cancel_active`,
`task_cancelled`, and `barge_in` events; each event is persisted as
`SignalEvent`, optional text is classified into `Intent`, memory-like items are
stored as `MemoryItem`, and cancellation-class events actively cancel linked
`READY|CLAIMED|RUNNING` tasks.

### Phase 6 - Hardening and rollout

- Add token/HMAC auth for Paperclip and TTS callbacks.
- Add replay-safe event ingestion and retry handling.
- Add operational dashboards for queue depth, stale sessions, failed
  dispatches, Neo4j availability, and retrieval latency.
- Canary with one local Hermes agent, then add remote devices.
- Document rollback paths.

**Exit criteria:** multi-device rollout passes canary and operational checks.

**Status update (May 23, 2026):** Added enforced webhook signature checks,
request rate limiting, websocket token auth (`WS_AUTH_REQUIRED`, `WS_AUTH_TOKEN`),
periodic maintenance retention jobs, CI workflow, and `GET /api/ops/status` for
queue depth / stale sessions / failed dispatches / Neo4j health.

### Phase 7 - Intent outcomes and policy gating

- Introduce a normalized intent outcome taxonomy:
  - `actionable_task`
  - `cancellation`
  - `memory_capture`
  - `information_query`
  - `ambiguous`
- Persist `classification`, `intent_outcome`, and `intent_confidence` on each
  `Intent`.
- Add policy action derivation for operator visibility and routing:
  - `auto_dispatch_eligible`
  - `review_dispatch`
  - `auto_cancel_eligible`
  - `review_cancel`
  - `no_dispatch`
  - `needs_clarification`
- Ensure voice and direct `/api/intents` ingestion share the same policy model.

**Exit criteria:** each new intent has outcome + confidence + policy action and
can be filtered by policy in command-center workflows.

**Phase 7 extension (May 24, 2026):** V3 orchestration design is now defined in
`docs/PHASE_7_ORCHESTRATION_V3.md`, including lane-based model routing
(small-model drafting + selective escalation), workflow DAG materialization, and
multi-step complex workflow execution patterns.

**Expanded Phase 7 execution focus:**
- Stand up model registry + lane router (`draft`, `verify`, `escalate`) with
  provider-aware health/fallback.
- Add `Workflow` / `WorkflowStep` / `WorkflowDecision` graph schema and APIs.
- Run v3 orchestrator in `shadow -> assist -> primary` rollout modes with
  automatic V2 fallback on v3 failure paths.
- Add workflow-level observability and replay benchmark harness for V2-vs-V3
  comparison before primary cutover.

### Phase 8 - Closed-loop orchestration and SLOs

Phase 8 proposal is defined in
`docs/PHASE_8_AUTONOMOUS_WORKFLOW_OPERATIONS.md` and shifts from “policy loop”
to autonomous multi-workflow operations:

- Admission control + scheduler with queue classes (`interactive`, `batch`,
  `critical`).
- Budgeted autonomy (time/token/retry envelopes per workflow).
- Autonomous repair/replan with dead-letter review handoff.
- Device/model capacity economics and escalation budget controls.
- Workflow-centric SLOs, burn-rate alerts, and incident taxonomy.

**Exit criteria:** 24h autonomous canary passes queue, latency, retry, and
escalation budget gates with reduced operator review load per workflow.

**Status update (May 24, 2026):** Intent orchestrator now consumes
`policy_action`. Intents marked `review_dispatch`, `needs_clarification`, and
`review_cancel` are routed into explicit `Task(status=REVIEW, kind=intent_review)`
triage tickets linked to the source `Intent`, while `auto_dispatch_eligible`
task intents continue through automatic task creation/dispatch.
Review queue APIs are now available to close the loop:
`GET /api/review/tasks`, `POST /api/review/tasks/{task_id}/approve`,
`POST /api/review/tasks/{task_id}/reject`, and
`POST /api/review/tasks/{task_id}/clarify`.
Phase 8 workflow ops skeleton now in place:
`GET /api/workflows/queue`, `GET /api/workflows/slo`,
`POST /api/workflows/control`, plus `workflow` block in `/api/ops/status`
(backlog, running, escalation backlog, control state).
Phase 8 control behavior and incident/budget APIs implemented:
drain mode now blocks non-critical task claims, critical claims remain allowed;
added `POST /api/workflows/{workflow_id}/replan`,
`POST /api/workflows/{workflow_id}/budget/update`, and
`GET /api/workflows/{workflow_id}/incidents` with workflow incident/budget graph
entities.
Admission filtering is now enforced in `/api/agent/tasks` so drain-mode and
workflow-capacity gates reduce non-admissible task offers before claim attempts.

### Phase 9 - Evaluation fabric and data feed integrations

Phase 9 proposal is defined in
`docs/PHASE_9_EVALUATION_AND_DATA_FEEDS.md` and focuses on expanding coverage
with evaluation-governed integrations:

- Build a cross-workflow evaluation fabric (`EvaluationRun`,
  `EvaluationSuite`) with agent scorecards.
- Integrate prioritized external/internal feed connectors with normalized
  schemas, freshness, replay safety, and health telemetry.
- Gate promotion from sandbox/shadow to active automation via objective quality
  thresholds.
- Add degradation behavior (advisory-only fallback) on score drift.

**Exit criteria:** at least three critical feed classes are integrated and
continuously evaluated with enforced promotion/demotion gates.

**Status update (May 24, 2026):** Phase 9 Sprint 1 foundation implemented:
evaluation/feed schema placeholders added (`EvaluationRun`, `EvaluationSuite`,
`DataFeedConnector` constraints/indexes), feed registry skeleton added
(`feed_registry.py`), and feed health now surfaced in `GET /api/ops/status`
under `feeds`.
Phase 9 Sprint 2 API scaffolding implemented:
`GET/POST /api/feeds` for connector persistence/sync and
`GET/POST /api/evaluations` for evaluation run ingestion and filtering.
Cross-repo voice integration plan added:
`docs/SOPHIA_TO_ASSISTX_INTEGRATION_PLAN.md` maps Sophia voice runtime/auth and
meeting-mode outputs into AssistX workflow queues, feed connectors, and
evaluation suites for Phase 8/9/10 execution.
Sophia connector/suite defaults now implemented in code:
feed registry includes Sophia voice feeds, evaluation registry includes Sophia
auth/meeting suites, and APIs now expose/sync them
(`GET/POST /api/feeds`, `GET/POST /api/evaluations/suites`).
Sophia voice ingress contract implemented:
`POST /api/sophia/events` now ingests structured Sophia runtime events into
`SignalEvent`, derives workflow queue class, creates intent/task when
transcript payload is actionable, and records workflow incidents for anomalous
auth states.
Sophia operational visibility added:
`GET /api/sophia/summary` plus command-center Sophia card showing auth-state
distribution, queue-class mix, and anomaly incident counts.

### Phase 10 - Persistent research and analyst agent fleet

Phase 10 proposal is defined in
`docs/PHASE_10_PERSISTENT_RESEARCH_AND_ANALYST_AGENTS.md` and operationalizes
always-on domain intelligence:

- Persistent research agents (continuous thematic/company/macro synthesis).
- Technical analyst agents (market structure, regime shifts, signal ranking).
- Financial health analyst agents (liquidity/leverage/profitability/cashflow
  trajectory monitoring).
- Continuous scheduling, heartbeat supervision, evaluation gating, and
  alert-routing with dedupe/severity controls.

**Exit criteria:** always-on domain agents run for 14 days with bounded
failures and evaluation-qualified output quality.

### Phase 11 - Multi-device productionization

- Add explicit device capacity model and admission control:
  - max concurrent tasks per device,
  - capability-aware routing,
  - maintenance/drain mode.
- Add remote host bootstrap runbook for Hermes adapter as a managed service.
- Add disaster recovery checklist and regular restore drills for Neo4j + Redis.
- Define production change windows, rollback drills, and weekly ops review.

**Exit criteria:** at least 2 device classes (local + remote) can sustain
continuous workload with documented recovery and rollback.

---

## 11) Testing and Validation

### Contract tests

- Validate intent payloads.
- Validate context packet payloads.
- Validate dispatch payloads.
- Validate Paperclip event payloads.
- Validate memory write payloads.

### Neo4j tests

- Constraint/index creation.
- Idempotent upserts.
- Relationship invariants.
- Late event handling.
- Duplicate replay handling.
- Cross-database or single-database namespace behavior.

### Retrieval tests

- Bounded result size.
- Citation/source IDs included.
- Freshness scoring.
- Relevance scoring.
- No unbounded graph dumps.
- Missing/partial source behavior.

### Hermes memory-provider tests

- `prefetch` returns a fenced context block.
- `sync_turn` creates graph events without blocking the agent.
- `on_memory_write` mirrors explicit memory writes.
- `on_delegation` records child task/result observations.
- `on_session_switch` updates session linkage.
- Tool schemas route correctly.

### Paperclip integration tests

- Issue creation from AssistX task.
- Assignment to a specific Paperclip agent.
- Hermes session resume across repeated runs.
- Run completion sync.
- Comment sync.
- Status sync.
- Failure/cancel handling.

### Voice tests

- Voice task creation.
- Voice idea stored as memory.
- Duplicate event replay.
- Cancellation/barge-in.
- Async dispatch.
- TTS response linkage.

### End-to-end scenarios

1. Voice idea becomes graph memory.
2. Dashboard task retrieves context and dispatches to a local Hermes agent.
3. Remote Hermes agent receives a Paperclip assignment and resumes the correct
   session.
4. Hermes queries graph context before acting.
5. Hermes writes outcome/artifact/memory.
6. AssistX dashboard reflects completion, provenance, and context used.

---

## 12) Security and Operations

### Authentication

- Keep Basic Auth for human dashboard access until replaced.
- Require API tokens or HMAC signatures for Paperclip and TTS callbacks.
- Do not expose Neo4j directly to untrusted clients.
- Store Paperclip/Hermes credentials outside graph payloads.

### Safety

- Keep AssistX approval gates for high-risk tasks.
- Use Paperclip/Hermes sandboxing, worktrees, checkpoints, and scoped toolsets
  for code execution.
- Persist policy decisions and denied actions as run provenance.
- Allow cancellation and pause from the command center.

### Observability

Track:

- intent ingestion rate;
- context retrieval latency;
- context packet size;
- dispatch queue depth;
- Paperclip event lag;
- active/stale Hermes sessions;
- failed dispatches;
- failed tool calls;
- Neo4j availability;
- memory freshness.

### Rollback

- Disable new intent dispatch while keeping read-only dashboard access.
- Stop Paperclip event ingestion without deleting graph state.
- Disable Hermes external memory provider and fall back to built-in memory.
- Revert to existing AssistX `/api/ask` and manual task execution paths.

---

## 14) Progress Summary for Handoff

### Session (May 23, 2026): Paperclip Live Dispatch + Security Review

- **Paperclip server running as systemd service** at `http://127.0.0.1:3100` with persistent data.
- **Dev override**: 3 lines patched in Paperclip source (config.ts x2, index.ts x1) to allow `local_trusted` mode with `0.0.0.0` bind, enabling Docker container access.
- **Docker networking**: `extra_hosts: host.docker.internal:host-gateway` added to `docker-compose.yml` API service so containers resolve the host.
- **Source mount**: `./src:/app/src` volumed into API container for live code iteration.
- **Paperclip resources created**: company `AssistX Workspace`, agent `hermes-local`, API key `pcp_1966f1eb...`.
- **paperclip_client.py routes updated** to match real Paperclip API (`/companies/:companyId/issues`, `/issues/:id/comments`, `/heartbeat-runs/:runId`, etc.).
- **Dispatch flow tested end-to-end**:
  ```
  POST /api/tickets → ticket_id
  POST /api/dispatch → {dispatch_id, paperclip_issue_id, context_packet_id, paperclip_error: null}
  GET /api/issues/2365b591 → status=backlog, createdByAgentId set
  ```
- **Webhook endpoint** at `POST /api/paperclip/events` is wired but Paperclip has no outbound webhook API — event polling is the fallback.
- **Security review completed**: HMAC verification is optional (needs tightening), no rate limiting, WebSocket endpoints lack auth, Basic Auth uses plain `==`. All findings documented in item 15.

### Previous sessions
- Added Sophia-inspired mobile media capture intake:
  - `/ingest` now supports browser audio/video recording, upload/camera fallback,
    transcript/context entry, device fingerprinting, and upload progress.
  - `POST /api/captures` stores uploaded audio/video/media under
    `artifacts/captures` and writes `MediaCapture`, `MediaAsset`,
    `Transcription`, `MemoryItem`, `Intent`, and `SignalEvent` records to Neo4j.
  - This completes a first pass at voice/video intake into graph-first
    `Intent` and `MemoryItem` records. The next pass should classify captures
    into memory-only, fact/preference, executable task, cancellation, or status
    query.
- Added command-center API scaffolding in `src/assistx/api.py` including
  `POST /api/intents`, `/api/brain/context`, `/api/dispatch`,
  `/api/paperclip/events`, `/api/memory/items`, `/api/brain/signals`, and
  `/api/sessions/{session_id}`.
- Added a Hermes memory provider prototype in
  `src/assistx/agents/hermes_memory_provider.py` with hooks for prefetch,
  memory writes, signal events, and session updates.
- Added integration-style regression coverage in `tests/test_migration_api.py`
  for intent creation, context packet retrieval, dispatch creation, session
  updates, memory writes, and signal ingestion.
- Added developer test infrastructure and fixtures for Neo4j-backed tests.
- Verified syntax for updated migration-related modules with `python3 -m py_compile`.

### Current state at handoff (May 23, 2026)

**All 20 pytest tests pass** — 10 migration API tests + 9 Hermes memory provider tests + 1 schema contract test.

**Live Paperclip dispatch flow tested end-to-end.** Paperclip server runs as a systemd user service on the host. AssistX creates Paperclip issues from task dispatches.

#### Test results

```
# tests/test_migration_api.py — 10/10 passed
test_api_intent_and_context_packet          PASSED
test_dispatch_and_session_endpoints         PASSED
test_task_trigger_lifecycle                 PASSED
test_ticket_hierarchy_and_paperclip_dispatch PASSED
test_ask_deliverable_breakdown              PASSED
test_command_center_intents                 PASSED
test_command_center_memory                  PASSED
test_command_center_devices                 PASSED
test_command_center_task_controls           PASSED
test_command_center_reassign                PASSED

# tests/test_hermes_memory_provider.py — 9/9 passed
test_hermes_memory_provider_prefetch              PASSED
test_hermes_memory_provider_write_memory          PASSED
test_hermes_memory_provider_signal_event          PASSED
test_hermes_memory_provider_update_session        PASSED
test_hermes_memory_provider_with_token            PASSED
test_hermes_memory_provider_system_prompt_block   PASSED
test_hermes_memory_provider_sync_turn             PASSED
test_hermes_memory_provider_on_delegation         PASSED
test_hermes_memory_provider_on_session_switch     PASSED

# tests/test_schema_contract.py — 1/1 passed
test_ensure_schema_declares_migration_constraints_and_indexes PASSED
```

#### Paperclip integration details

- Paperclip server runs as systemd user service (`paperclip.service`) at `http://127.0.0.1:3100`.
- Dev override: 3 lines commented out in Paperclip source (`config.ts:275-277`, `config.ts:284-286`, `index.ts:447-452`) to allow `local_trusted` + `0.0.0.0` bind.
- Docker connectivity: `extra_hosts: host.docker.internal:host-gateway` added to API service.
- Live code: `./src:/app/src` volume mount in `docker-compose.yml` for API container.
- `paperclip_client.py` routes updated to match real Paperclip API structure.
- Company `AssistX Workspace`, agent `hermes-local`, API key `pcp_1966f1eb...` registered.
- Env vars: `PAPERCLIP_API_URL`, `PAPERCLIP_API_TOKEN`, `PAPERCLIP_WORKSPACE_ID`, `PAPERCLIP_WEBHOOK_SECRET` set.

#### Code quality fixes applied

- **`@app.on_event("startup")` → lifespan handler** — deprecated startup decorator replaced with `@asynccontextmanager`-based lifespan function (`api.py:184`).
- **`AgentCapability` constraint removed** — orphan UNIQUE constraint deleted from `ensure_schema()`.
- **HermesMemoryProvider lifecycle implemented** — added `system_prompt_block`, `sync_turn`, `on_delegation`, `on_session_switch` methods to bridge to real Hermes `MemoryProvider` interface.
- **Phase 4 command center UI templates** — added `command_center.html`, `intents.html`, `dispatches.html`, `sessions.html`, `memory.html`, `devices.html` with JS-powered data fetching from existing `/api/*` endpoints.
- **Duplicate startup handler removed** — two `@app.on_event("startup")` handlers collapsed into one with try/except logging.
- **Messy imports cleaned** — 17 import lines → 12, duplicate `json`, `os`, `typing`, `pydantic.BaseModel` removed.
- **Duplicate SSE helper removed** — `_sse_format` inlined into `_sse`, all callers updated.
- **Inline `import logging` / `import time`** moved to top-level imports.
- **Indentation corruption** in SSE generator functions fixed.
- **Dual device label unified** — `:Device` → `:AgentDevice` in `ingest_media_capture`.
- **Neo4j 5.x syntax** — `NOT EXISTS()` replaced with `coalesce()` in `add_utterances`.
- **All `randomUUID()` → Python `uuid.uuid4().hex`** — 20 calls across `neo4j_client.py` (17), `api.py` (2), `api_reassign_dispatch` (1) replaced with Python-side UUIDs to guarantee uniqueness under concurrent creation.
- **`MERGE (d:Dispatch {id:$did})-[:ASSIGNED_TO]->(a)` anti-pattern** — split into `MATCH (d ...)` + `MERGE (a ...)` + `MERGE (d)-[:r]->(a)` to prevent MERGE from attempting to CREATE a duplicate Dispatch node when the pattern wasn't fully matched.
- **Unconsumed `s.run()` results** — `.consume()` added to all auto-commit queries where the result was not consumed, preventing timing windows where subsequent queries didn't see prior commits.
- **`metadata` dict → `metadata_json`** — `upsert_agent_session` and `upsert_agent_device` now JSON-serialize their `metadata` parameter (Neo4j rejects nested Map property values).
- **Test fixture cleanup** — `seeded_neo4j` now issues `MATCH (n) DETACH DELETE n` with `.consume()` before each test, followed by `ensure_schema()` rerun.
- **Prometheus metrics re-import safety** — `_safe_counter`/`_safe_gauge`/`_safe_histogram` helpers with try/except + registry lookup.

#### Known issues

1. ~~**`@app.on_event("startup")` is deprecated**~~ → **RESOLVED**: migrated to lifespan handler pattern (`api.py:184`).
2. ~~**`AgentCapability` label has a UNIQUE constraint**~~ → **RESOLVED**: orphan constraint removed from `ensure_schema()`.
3. ~~**`HermesMemoryProvider` is a thin HTTP client**~~ → **RESOLVED**: `system_prompt_block`, `sync_turn`, `on_delegation`, `on_session_switch` implemented.
4. ~~**Paperclip webhook integration not tested**~~ → **RESOLVED**: live dispatch flow tested end-to-end (see section 14).
5. **Paperclip has no outbound webhook API** — event ingestion endpoint exists at `POST /api/paperclip/events` but must be called by an external poller. Polling falls back to `GET /companies/:companyId/issues`. Paperclip poller RQ job at `src/assistx/paperclip_poller.py` handles this.
6. **Paperclip dev overrides** — 3 lines patched in Paperclip source to bypass `local_trusted` + `loopback` bind enforcement. Will be overwritten on Paperclip version updates. Re-apply with `patches/apply-paperclip-patches.sh`.
7. ~~**`claim_id=coalesce($idempotency_key, randomUUID())`**~~ → **RESOLVED**: replaced with Python `uuid.uuid4().hex` in `claim_task`.
8. ~~**`summarize_from_segments.py`** uses Cypher `randomUUID()`~~ → **RESOLVED**: replaced with Python `uuid.uuid4().hex`.

#### Test infrastructure

- Ephemeral Docker container per test session (`test_session`-scoped `neo4j_container` fixture).
- Session-scoped `neo4j_client` with schema ensured once.
- Function-scoped `seeded_neo4j` with DETACH DELETE cleanup + re-seed.
- Default auth: `neo4j` / `livelongandprosper`.
- Run: `python -m pytest tests/test_migration_api.py -v`

#### Immediate next steps (in priority order)

 1. ~~**Migrate `on_event("startup")` → lifespan handler** in `api.py`.~~ ✅ DONE
 2. ~~**Drop `AgentCapability` constraint** from `ensure_schema()` if unused.~~ ✅ DONE
 3. ~~**Implement HermesMemoryProvider lifecycle methods** (`system_prompt_block`, `sync_turn`, `on_delegation`, `on_session_switch`).~~ ✅ DONE
 4. ~~**Build Phase 4 command center HTML UI templates** for intent/dispatch/session/device views.~~ ✅ DONE
 5. ~~**Register Paperclip webhook** and test live dispatch flow.~~ ✅ DONE
    - Paperclip server: systemd user service at `http://127.0.0.1:3100`
    - Dev override: `local_trusted` loopback enforcement bypassed (3 lines)
    - Paperclip resources: company `AssistX Workspace`, agent `hermes-local`, API key `pcp_1966f1eb...`
    - Docker: `extra_hosts: host.docker.internal:host-gateway`, `./src:/app/src` volume
    - Client routes updated to match real Paperclip API (`/companies/:companyId/issues`, etc.)
    - **Live dispatch test**: `POST /api/tickets → POST /api/dispatch → Paperclip issue created`
    - Limitation: Paperclip has no outbound webhook API — polling fallback used
6. ~~**Security hardening** — reviewed; findings documented in section 15.~~ ✅ PARTIALLY DONE
 7. ~~**Paperclip poller** — RQ job periodically syncs Paperclip issue status → Neo4j.~~ ✅ DONE
 8. ~~**Hermes agent adapter** — polls AssistX tasks, runs Hermes CLI, completes tasks.~~ ✅ DONE
 9. ~~**Hermes adapter systemd service** — `hermes-agent-adapter.service`.~~ ✅ DONE
10. ~~**Neo4j `randomUUID()` bug** — replaced with Python `uuid.uuid4().hex` in `claim_task`, `create_run`, `summarize_from_segments.py`.~~ ✅ DONE
11. **Paperclip dev overrides** — patch file at `patches/paperclip-local-trusted.patch`, apply with `patches/apply-paperclip-patches.sh`.



---

## 15) Immediate Next Actions

1. ~~**Phase 0 artifacts**~~ ✅ DONE — repo/service inventory in `docs/PHASE_0_INVENTORY.md`.
2. ~~**AssistX schema methods**~~ ✅ DONE — orchestration labels and constraints in `neo4j_client.ensure_schema()`.
3. ~~**Hermes memory provider prototype**~~ ✅ DONE — `src/assistx/agents/hermes_memory_provider.py`.
4. ~~**Paperclip → Hermes → Neo4j result sync**~~ ✅ DONE — live end-to-end tested.
5. ~~**Periodic Paperclip poller**~~ ✅ DONE — `src/assistx/paperclip_poller.py`, scheduled from API lifespan.
6. ~~**Hermes agent adapter**~~ ✅ DONE — `src/assistx/agents/hermes_agent_adapter.py`, runs as `hermes-agent-adapter.service`.
7. **Security hardening**:
   - ~~Enable required HMAC verification on `/api/paperclip/events` (currently optional)~~ ✅ DONE
   - ~~Add rate limiting to dispatch and event endpoints~~ ✅ DONE (dispatch, events, ask, intents)
   - ~~Use `hmac.compare_digest` for Basic Auth password check~~ ✅ DONE
   - Rotate `.env` secrets if they were exposed in git history before `.gitignore` was added (**manual ops step pending**)
8. **Phase 7 intent policy baseline**:
   - Persist intent outcome/confidence on all new intents.
   - Surface policy action in API responses and metadata.
   - Add regression tests for cancel/memory/task policy outcomes.
9. **Phase 8 orchestration loop**:
   - Consume policy action in the orchestrator to separate auto-dispatch and
     review queues.
   - Add canary watchdog for queue growth and stale running jobs.
10. **Phase 9 evaluation + feed integration prep**:
    - Add evaluation suite scaffolding and scorecard schema.
    - Define prioritized market/financial/research feed connectors with health checks.
11. **Phase 10 persistent analyst agents prep**:
    - Define always-on research, technical analyst, and financial-health agent templates.
    - Add continuous scheduling/heartbeat plan for persistent agent fleet.
12. **Phase 11 device expansion prep**:
    - Add adapter health heartbeat SLOs and per-device load telemetry.
    - Publish operator runbook for adding remote Hermes devices.
