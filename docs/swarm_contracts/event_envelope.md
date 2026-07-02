# Unified Event Envelope Contract

_Last updated: 2026-06-08_

## Purpose

All swarm services should publish important state transitions to AssistX using one event envelope.

The event envelope supports:

- idempotent replay
- local outbox recovery
- cross-repo provenance
- auditability
- graph reconciliation
- cross-repo trace correlation via `correlation_id`

---

## Envelope schema

Schema version `2026-06-08.v1` adds `correlation_id`, `actor`, and `links` fields for cross-repo tracing. Schema version `1.0` remains supported for backward compatibility.

```json
{
  "event_id": "string",
  "event_type": "string",
  "source_repo": "auto-assist | Sophia | auto-ingest",
  "source_service": "string",
  "node_id": "string",
  "occurred_at": "ISO-8601",
  "idempotency_key": "string",
  "schema_version": "2026-06-08.v1",
  "subject": {
    "kind": "task | file | capture | utterance | detection | artifact | model | node | batch",
    "id": "string"
  },
  "payload": {},
  "artifact_refs": [],
  "privacy": {
    "pii": true,
    "privacy_class": "private | sensitive | public | unknown",
    "retention_class": "ephemeral | keep | protected | evidence"
  },
  "correlation_id": "stable-id-linking-auth-dispatch-route-assignment",
  "actor": {
    "user_id": "scott",
    "device_id": "browser-or-phone-device-id",
    "auth_state": "accepted | rejected | not_required"
  },
  "links": {
    "dispatch_id": "optional",
    "intent_id": "optional",
    "task_id": "optional",
    "route_id": "optional",
    "assignment_id": "optional"
  },
  "metadata": {
    "request": {},
    "task": {},
    "context": {}
  }
}
```

Optional `metadata` is reserved for shared request/task context consumed by auto-router and auto-assign. AssistX may derive and persist the request block even when producers omit it; task candidates may also include a task block.

---

## Required fields

| Field | Required | Notes |
|---|---:|---|
| `event_id` | yes | UUID or deterministic replay-safe ID |
| `event_type` | yes | Namespaced event type |
| `source_repo` | yes | Which repo produced event |
| `source_service` | yes | Service/module name |
| `node_id` | yes | Swarm node ID |
| `occurred_at` | yes | Source occurrence time |
| `idempotency_key` | yes | Used for replay dedupe |
| `schema_version` | yes | `1.0` (legacy) or `2026-06-08.v1` (with correlation_id) |
| `subject` | yes | Main object the event concerns |
| `payload` | yes | Event-specific body |
| `artifact_refs` | yes | Empty list allowed |
| `privacy` | yes | Required for retention/safety |
| `correlation_id` | no* | Required for cross-repo tracing (`2026-06-08.v1`) |
| `actor` | no* | User/device/auth context (`2026-06-08.v1`) |
| `links` | no* | Downstream ID references (`2026-06-08.v1`) |

_*Required when `schema_version` is `2026-06-08.v1`._

---

## Event type namespaces

### Voice/Sophia

```text
voice.capture.created
voice.stt.completed
voice.auth.decision
voice.quick_input.created
voice.registration.requested
voice.response.spoken
```

### AssistX

```text
assistx.task.created
assistx.task.claimed
assistx.task.completed
assistx.task.failed
assistx.approval.requested
assistx.approval.granted
assistx.dispatch.created
assistx.agent_run.completed
```

### Canonical cross-repo event types (2026-06-08.v1)

These are the canonical event types used across Sophia, AssistX, auto-router, and auto-assign:

**Voice/auth events:**
```text
voice.auth.requested
voice.auth.accepted
voice.auth.rejected
voice.auth.error
```

**Dispatch/control events:**
```text
dispatch.requested
dispatch.accepted
dispatch.rejected
dispatch.cancelled
```

**Routing events:**
```text
route.requested
router.route_decision
route.selected  # legacy alias supported for backward compatibility
route.failed
route.blocked
```

**Assignment events:**
```text
assignment.requested
assignment.recommended
assignment.claimed
assignment.heartbeat
assignment.released
assignment.completed
assignment.failed
assignment.expired
```

**Ingest/context events:**
```text
ingest.context.available
ingest.context.updated
ingest.evidence.linked
ingest.evidence.missing
```

### auto-ingest

```text
ingest.batch.started
ingest.file.seen
ingest.file.normalized
ingest.transcript.completed
ingest.memory_candidate.created
ingest.memory_candidate.promoted
ingest.batch.review_ready
ingest.batch.completed
```

### Vision/birdcam

```text
vision.detection.created
vision.clip.created
vision.event.protected
vision.outbox.replayed
```

### Swarm/node/model

```text
swarm.node.registered
swarm.node.heartbeat
swarm.endpoint.probed
model.endpoint.discovered
model.benchmark.completed
```

---

## Example: Sophia quick input

```json
{
  "event_id": "voice-20260526-001",
  "event_type": "voice.quick_input.created",
  "source_repo": "Sophia",
  "source_service": "voice-agent",
  "node_id": "x1-370",
  "occurred_at": "2026-05-26T18:00:00-04:00",
  "idempotency_key": "capture-abc123:utterance-1",
  "schema_version": "1.0",
  "subject": {
    "kind": "utterance",
    "id": "utterance-1"
  },
  "payload": {
    "text": "Summarize open tasks for today.",
    "auth_state": "authenticated_scott",
    "speaker_identity": "scott",
    "speaker_confidence": 0.91
  },
  "artifact_refs": [],
  "privacy": {
    "pii": true,
    "privacy_class": "private",
    "retention_class": "keep"
  }
}
```

---

## Example: auto-ingest memory candidate

```json
{
  "event_id": "ingest-candidate-001",
  "event_type": "ingest.memory_candidate.created",
  "source_repo": "auto-ingest",
  "source_service": "historical-ingest",
  "node_id": "deathstar-XPS-8920",
  "occurred_at": "2026-05-26T20:00:00-04:00",
  "idempotency_key": "batch-001:segment-123",
  "schema_version": "1.0",
  "subject": {
    "kind": "batch",
    "id": "batch-001"
  },
  "payload": {
    "classification": "scott_speech",
    "candidate_type": "opinion",
    "confidence": 0.82,
    "text": "Example candidate memory text."
  },
  "artifact_refs": [
    {
      "artifact_id": "artifact-123",
      "kind": "transcript",
      "storage_root": "nas1",
      "relative_path": "fileserver/dashcam/transcriptions/example.json"
    }
  ],
  "privacy": {
    "pii": true,
    "privacy_class": "private",
    "retention_class": "keep"
  }
}
```

---

## Ingestion endpoint

```http
POST /api/events
```

Response:

```json
{
  "accepted": true,
  "event_id": "voice-20260526-001",
  "deduped": false,
  "graph_reconciled": true
}
```

---

## Idempotency rules

- `event_id` must be unique.
- `idempotency_key` must be stable for replayable events.
- Replaying the same event must not create duplicate graph nodes.
- If same key arrives with different payload hash, store conflict event and require review.

---

## Local outbox rules

Services should write events to local outbox before or during delivery attempts.

Outbox record:

```yaml
outbox_id: string
event_id: string
payload_json: string
attempt_count: int
last_attempt_at: optional ISO-8601
status: pending | delivered | failed | conflict
```

---

## Implementation checklist

- [x] Add `/api/events` endpoint in AssistX.
- [x] Add event schema validation (supports `1.0` and `2026-06-08.v1`).
- [x] Add idempotency/dedupe table or Neo4j constraint.
- [ ] Add event payload hash conflict detection.
- [ ] Add graph reconciliation handlers by event namespace.
- [ ] Add local outbox client library for Sophia and auto-ingest.
- [ ] Add tests for replay, conflict, and malformed payloads.
- [x] Add `correlation_id`, `actor`, `links` fields to EventEnvelopeIn (`2026-06-08.v1`).
- [x] Add `TraceEvent`/`TraceGroup` persistence in Neo4j with indexes.
- [x] Add `GET /api/traces/{correlation_id}` trace query endpoint.
- [x] Add `POST /api/traces/{correlation_id}/events` trace event append endpoint.
- [x] Normalize voice events to canonical trace types in `/api/voice/events`.
- [x] Emit `voice.auth.*` and `dispatch.*` trace events from voice ingestion.
