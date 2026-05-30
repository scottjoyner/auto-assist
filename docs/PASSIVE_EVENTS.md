# Passive Agent Event History

## 1. Purpose

Passive agent events provide a lightweight append-only audit trail for the passive-work coordination system.

They answer questions like:

- which agent claimed a task;
- when a claim was renewed;
- why a task was released;
- which claims expired;
- which safety rollback happened.

This avoids relying only on mutable `Task` and `AgentHeartbeat` properties when debugging passive agent behavior.

## 2. Endpoint

```text
GET /api/agents/passive-events
GET /api/agents/passive-events?agent_id=gemini-cli-x1-370
GET /api/agents/passive-events?task_id=task-123
GET /api/agents/passive-events?event_type=passive_claim.created
```

Response shape:

```json
{
  "items": [
    {
      "event_id": "uuid",
      "event_type": "passive_claim.created",
      "agent_id": "gemini-cli-x1-370",
      "task_id": "task-123",
      "claim_id": "claim-uuid",
      "lease_id": "lease-uuid",
      "status": "CLAIMED_PASSIVE",
      "action": "passive_claimed",
      "result": null,
      "summary": null,
      "metadata": {
        "mode": "review_only",
        "ttl_seconds": 1800
      },
      "created_at_ts": 1760000000000
    }
  ],
  "count": 1,
  "summary": {
    "total": 1,
    "by_type": {
      "passive_claim.created": 1
    },
    "by_agent": {
      "gemini-cli-x1-370": 1
    }
  },
  "read_only": true
}
```

## 3. Recorded event types

| Event type | Meaning |
|---|---|
| `passive_claim.created` | Agent created a review-only passive claim |
| `passive_claim.renewed` | Agent renewed an active passive claim |
| `passive_claim.released` | Agent released a passive claim |
| `passive_claim.expired` | Maintenance expired a stale claim |
| `passive_claim.rollback` | Claim was rolled back because safety/capability validation failed |

## 4. Best-effort behavior

Event recording is best-effort. If Neo4j event creation fails, heartbeat and passive-claim flows should continue whenever their primary operation succeeds.

The event history is for observability and audit, not for transactional locking.

## 5. Recommended operator checks

```bash
curl 'http://localhost:8000/api/agents/passive-events?limit=50' \
  -u admin:change-me | jq

curl 'http://localhost:8000/api/agents/passive-events?agent_id=gemini-cli-x1-370' \
  -u admin:change-me | jq

curl 'http://localhost:8000/api/agents/passive-events?task_id=task-123' \
  -u admin:change-me | jq
```

## 6. Boundary

Passive events do not execute or approve work. They only record passive coordination activity.
