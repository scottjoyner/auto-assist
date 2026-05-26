# Unified Event Envelope Contract

_Last updated: 2026-05-26_

## Purpose

All swarm services should publish important state transitions to AssistX using one event envelope.

The event envelope supports:

- idempotent replay
- local outbox recovery
- cross-repo provenance
- auditability
- graph reconciliation

---

## Envelope schema

```json
{
  "event_id": "string",
  "event_type": "string",
  "source_repo": "auto-assist | Sophia | auto-ingest",
  "source_service": "string",
  "node_id": "string",
  "occurred_at": "ISO-8601",
  "idempotency_key": "string",
  "schema_version": "1.0",
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
  }
}
```

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
| `schema_version` | yes | Start with `1.0` |
| `subject` | yes | Main object the event concerns |
| `payload` | yes | Event-specific body |
| `artifact_refs` | yes | Empty list allowed |
| `privacy` | yes | Required for retention/safety |

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

- [ ] Add `/api/events` endpoint in AssistX.
- [ ] Add event schema validation.
- [ ] Add idempotency/dedupe table or Neo4j constraint.
- [ ] Add event payload hash conflict detection.
- [ ] Add graph reconciliation handlers by event namespace.
- [ ] Add local outbox client library for Sophia and auto-ingest.
- [ ] Add tests for replay, conflict, and malformed payloads.
