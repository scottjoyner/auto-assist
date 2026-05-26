# Task Authority Contract

_Last updated: 2026-05-26_

## Core decision

AssistX is the authoritative owner of task state across the offline swarm.

Other systems may execute work, cache state, or keep local outboxes, but final task truth lives in the AssistX control plane.

---

## Required task lifecycle

```text
queued -> claimed -> running -> completed
queued -> claimed -> running -> failed
queued -> awaiting_approval -> queued
queued -> cancelled
running -> blocked -> running
running -> cancelled
```

## States

| State | Meaning |
|---|---|
| `queued` | Work is available for dispatch. |
| `claimed` | A node has acquired a lease but has not started execution. |
| `running` | Work is actively executing. |
| `blocked` | Worker needs external dependency or context. |
| `awaiting_approval` | Human/Scott approval required. |
| `completed` | Work finished successfully. |
| `failed` | Work failed after retry policy or non-retryable error. |
| `cancelled` | Work was intentionally stopped. |

---

## Task schema

```yaml
task_id: string
task_type: string
created_at: ISO-8601
updated_at: ISO-8601
created_by: scott | system | registered_user | unknown_speaker
source_event_id: optional string
priority: low | normal | high | critical
risk_level: low | medium | high
status: queued | claimed | running | blocked | awaiting_approval | completed | failed | cancelled
requested_capabilities: list[string]
input_refs: list[ArtifactRef]
output_refs: list[ArtifactRef]
assigned_node_id: optional string
lease_expires_at: optional ISO-8601
approval_required: boolean
approval_id: optional string
retry_count: integer
max_retries: integer
error_summary: optional string
result_summary: optional string
```

---

## Worker claim protocol

Workers must claim tasks through AssistX before execution.

### Claim request

```http
POST /api/tasks/{task_id}/claim
```

```json
{
  "node_id": "demo-1",
  "capabilities": ["llm.chat", "draft.generate"],
  "lease_seconds": 900
}
```

### Heartbeat

```http
POST /api/tasks/{task_id}/heartbeat
```

```json
{
  "node_id": "demo-1",
  "status_message": "drafting response",
  "progress": 0.4
}
```

### Complete

```http
POST /api/tasks/{task_id}/complete
```

```json
{
  "node_id": "demo-1",
  "result_summary": "Draft generated and saved.",
  "output_refs": []
}
```

### Fail

```http
POST /api/tasks/{task_id}/fail
```

```json
{
  "node_id": "demo-1",
  "error_summary": "Model endpoint unavailable.",
  "retryable": true
}
```

---

## Lease behavior

- Claims create a time-limited lease.
- Heartbeats extend the lease.
- Expired leases return task to `queued` unless retry budget is exhausted.
- A node cannot complete a task it does not currently lease.
- Local outbox replay must include lease/task identifiers.

---

## Approval model

### Scott authenticated

Low-risk actions may auto-approve.

### Admin override

`admin_voice_override` may auto-approve low-risk actions if the override is accepted.

### Registered or unknown speakers

All actions require Scott approval.

### High-risk actions

High-risk actions require explicit approval even for Scott unless a future policy grants narrower exceptions.

---

## Low-risk action examples

- create a note
- create a draft
- summarize context
- query Neo4j memory
- list task state
- classify a file
- enqueue a non-destructive ingest review

## High-risk action examples

- delete/move files
- send emails/messages externally
- change auth/network config
- publish content
- execute destructive shell commands
- expose local data outside Tailscale/LAN

---

## Paperclip mapping

Paperclip issues are optional mirrors.

```text
AssistX Task -> Paperclip Issue
```

AssistX task state wins if Paperclip state diverges.

---

## Implementation checklist

- [ ] Add task schema validation.
- [ ] Add fail endpoint if missing.
- [ ] Add lease expiry scanner.
- [ ] Add node/capability-aware task polling.
- [ ] Add approval policy engine.
- [ ] Add Paperclip mirror reconciliation.
- [ ] Add tests for replay-safe task completion.
