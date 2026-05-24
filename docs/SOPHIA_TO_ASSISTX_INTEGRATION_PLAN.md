# Sophia -> AssistX Integration Plan (Phase 8/9/10 Alignment)

Date: May 24, 2026

## 1. Why This Matters

The Sophia repo has already solved practical voice-runtime problems that map
directly to our current roadmap:

- authenticated voice identity workflow,
- stable STT -> auth -> LLM -> TTS pipeline contracts,
- containerized sidecar operation with health/readiness endpoints,
- long-form meeting-mode pipeline direction,
- voice insight tooling and memory-graph promotion patterns.

These are immediately useful for AssistX Phase 8 workflow operations and
Phase 9/10 feed + persistent analyst agent plans.

## 2. What Sophia Already Has (High-Value Assets)

From `~/git/Sophia/PLAN.md` and related docs:

1. Voice auth + enrollment runtime
- `/auth/verify`, `/voiceprints/enroll`, `/voiceprints/train-neo4j`
- practical threshold tuning from live captures
- explicit auth result semantics and score-driven UI feedback.

2. Stable runtime endpoints and sidecar posture
- `/healthz`, `/readyz`, `/status`, `/events`, `/ws`
- websocket protocol support (`native_ws`, `hermes_overlay_v1`)
- capture persistence and memory-graph connection checks.

3. Meeting-mode roadmap with concrete decomposition
- diarization + clustering pipeline design,
- speaker identification against enrolled voiceprints,
- action-item/decision extraction plan,
- graph storage of conversation artifacts.

4. Voice policy plan (auth vs response-voice separation)
- explicit auth-state taxonomy:
  - `authenticated_scott`
  - `not_scott_known`
  - `unknown_unverified`
- response-voice policy routing independent of auth identity.

## 3. Direct Tie-In to AssistX

## 3.1 Phase 8 tie-in (workflow operations)

Use Sophia sidecar as a first-class workflow producer:

- Source class: `voice_runtime`
- Queue class defaults:
  - realtime voice tasks -> `interactive`
  - long meeting processing -> `batch`
  - auth/policy anomalies -> `critical`

Wire Sophia events into AssistX `Task` + `WorkflowIncident`:

- auth failure spikes -> `WorkflowIncident(incident_type=auth_drift)`
- diarization ambiguity -> `WorkflowIncident(incident_type=context_miss)`
- repeated ws errors -> `WorkflowIncident(incident_type=transport_fault)`

## 3.2 Phase 9 tie-in (evaluation + feeds)

Treat Sophia outputs as a feed connector family:

- `voice-auth-feed`
- `meeting-transcript-feed`
- `speaker-timeline-feed`
- `voice-policy-decision-feed`

Evaluate continuously using Phase 9 evaluation fabric:

- auth precision/recall,
- diarization segment quality,
- action-item extraction quality,
- end-to-end latency for meeting ingest -> structured output.

## 3.3 Phase 10 tie-in (persistent agents)

Use Sophia-derived feeds to fuel always-on agents:

- Research agents ingest meeting/action-item streams.
- Technical/financial analysts consume spoken notes as additional signal inputs.
- Financial-health agents track management commentary/voice memos as context.

## 4. Integration Contract (Proposed)

## 4.1 Event envelope into AssistX

Required fields:

- `source`: `sophia_voice`
- `event_type`
- `event_id` (idempotency key)
- `session_id`
- `auth_state`
- `speaker_identity`
- `speaker_confidence`
- `policy_version`
- `payload` (transcript segments, diarization timeline, artifacts)

## 4.2 Mapping to existing AssistX nodes

- Auth/voice decisions -> `SignalEvent` + `MemoryItem`
- Long-form outputs -> `ContextPacket` references + `Task` payload attachments
- Evaluation snapshots -> `EvaluationRun`
- Connector health -> `DataFeedConnector`

## 5. Immediate Implementation Steps

1. Add Sophia feed connector entries into AssistX feed registry defaults. ✅ Implemented
2. Add `sophia_voice` event ingestion endpoint contract in AssistX docs. ✅ Implemented
3. Add evaluator suite definitions:
   - `sophia_auth_quality_daily`
   - `sophia_meeting_extraction_daily`
   ✅ Implemented via evaluation suite registry defaults/API sync.
4. Add queue-class assignment policy for Sophia-origin tasks:
   - realtime -> `interactive`
   - meeting batch -> `batch`
   - auth anomalies -> `critical`
5. Add command-center panel for Sophia auth-state and meeting pipeline health.

## 8. Current Implementation Status (May 24, 2026)

- Sophia feed connectors now appear in AssistX defaults and persist via
  `GET /api/feeds` + `POST /api/feeds`.
- Sophia evaluation suites are now defaulted and persisted via
  `GET /api/evaluations/suites` + `POST /api/evaluations/suites`.
- Ops status includes `feeds` and `evaluation_suites` visibility blocks for
  deployment health checks.
- Sophia event ingestion endpoint now available:
  - `POST /api/sophia/events`
  - maps event -> `SignalEvent`
  - derives queue class (`interactive`/`batch`/`critical`)
  - optionally creates intent/task from transcript payload
  - raises workflow incidents for anomalous auth states.
- Sophia operational summary endpoint now available:
  - `GET /api/sophia/summary`
  - reports auth-state mix, event-type distribution, queue-class distribution,
    and auth anomaly incident counts.
- Command center now surfaces Sophia summary metrics for quick operator review.

## 6. Risks and Mitigations

- Risk: schema drift across repos
  Mitigation: versioned envelope (`voice_event_schema_version`) and contract tests.

- Risk: auth threshold regression on new devices
  Mitigation: calibration runs tracked as `EvaluationRun` with rollback threshold.

- Risk: overloading interactive queue with long-form audio
  Mitigation: strict queue-class split + admission controls.

## 7. Success Criteria

1. Sophia events ingest idempotently into AssistX with full traceability.
2. Auth/meeting pipelines visible in AssistX ops dashboards.
3. Daily evaluation runs for Sophia data quality are green.
4. Persistent agents consume Sophia-derived feeds without queue/SLO regression.
