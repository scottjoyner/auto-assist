# Passive Claims

## 1. Purpose

Passive claims bridge the gap between heartbeat recommendations and future execution claims.

A passive claim lets an idle agent reserve a safe task for review/planning work without executing code, dispatching Paperclip, writing files, committing, pushing, or mutating external systems.

This gives the system a clean progression:

```text
heartbeat recommendation
  -> advisory lease
  -> passive review-only claim
  -> passive claim maintenance/expiry
  -> future approved execution claim
```

## 2. Endpoints

### Create passive claim

```text
POST /api/agents/passive-claim
```

Request:

```json
{
  "agent_id": "gemini-cli-x1-370",
  "task_id": "task-123",
  "lease_id": "optional-heartbeat-lease-id",
  "capabilities": ["docs", "code"],
  "mode": "review_only",
  "ttl_seconds": 1800,
  "operator_approved": false,
  "metadata": {
    "source": "agent-loop"
  }
}
```

Response:

```json
{
  "ok": true,
  "claim_id": "uuid",
  "task_id": "task-123",
  "agent_id": "gemini-cli-x1-370",
  "mode": "review_only",
  "status": "CLAIMED_PASSIVE",
  "review_only": true,
  "execution_allowed": false,
  "write_allowed": false,
  "contract": {
    "task_claim": "passive_review_only",
    "execution": "not_performed",
    "dispatch": "not_performed",
    "repo_write": "not_allowed",
    "operator_approval_required_for_execution": true
  }
}
```

### Release passive claim

```text
POST /api/agents/passive-claim/release
```

Request:

```json
{
  "agent_id": "gemini-cli-x1-370",
  "claim_id": "claim-uuid",
  "task_id": "task-123",
  "result": "completed_review",
  "summary": "Reviewed docs and identified stale TODOs. No files changed.",
  "metadata": {
    "source": "agent-loop"
  }
}
```

Results:

| Result | Next task status |
|---|---|
| `completed_review` | `REVIEW` |
| `interrupted` | `READY` |
| `abandoned` | `READY` |
| `released` | `READY` |
| `expired` | `READY` |

### List passive claims

```text
GET /api/agents/passive-claims?limit=50
GET /api/agents/passive-claims?agent_id=gemini-cli-x1-370&include_expired=true
```

Returns active passive claims and summary counts:

```json
{
  "items": [
    {
      "task_id": "task-123",
      "title": "Review docs",
      "claim_id": "uuid",
      "agent_id": "gemini-cli-x1-370",
      "mode": "review_only",
      "expired": false,
      "seconds_remaining": 1200
    }
  ],
  "count": 1,
  "summary": {
    "total": 1,
    "active": 1,
    "expired": 0,
    "review_only": 1,
    "claim_ready": 0
  },
  "read_only": true
}
```

### Expire stale passive claims

```text
POST /api/agents/passive-claims/expire?limit=50
```

This releases expired `CLAIMED_PASSIVE` tasks back to `READY` or their previous status. It also marks the owning `AgentHeartbeat` as idle/stale for the expired task.

This endpoint does not execute anything. It is safe maintenance for stale coordination state.

## 3. Safety rules

Passive claims reject tasks that are:

- not `READY` or `REVIEW`;
- already claimed;
- sensitive;
- local-only;
- missing required agent capabilities;
- requested in an invalid mode.

`claim_ready` mode requires `operator_approved=true`, but even then the endpoint still does not execute anything.

## 4. Neo4j mutations

Creating a passive claim updates only the selected task and heartbeat state:

```text
(Task).status = CLAIMED_PASSIVE
(Task).passive_claim_id = <claim_id>
(Task).passive_claim_agent_id = <agent_id>
(Task).passive_claim_expires_at_ts = <timestamp>
(AgentHeartbeat).status = busy
(AgentHeartbeat)-[:PASSIVELY_CLAIMED]->(Task)
```

Releasing a passive claim clears active passive-claim fields and writes last passive-claim metadata:

```text
(Task).status = READY or REVIEW
(Task).last_passive_claim_result = <result>
(Task).last_passive_claim_summary = <summary>
(AgentHeartbeat).status = idle
```

Expiring stale passive claims performs the same cleanup with result `expired`.

## 5. Boundary with execution

Passive claims do not:

- run code;
- dispatch Paperclip/Hermes;
- execute shell commands;
- write files;
- commit or push;
- approve production changes;
- override privacy or local-only flags.

Future execution claims should be implemented as a separate endpoint with stricter approval, sandbox, worktree, command allow-list, artifact capture, and operator review requirements.

## 6. Recommended agent loop

```text
POST /api/agents/heartbeat-plan status=idle
  -> plan.action review_next_candidate
  -> plan.lease
POST /api/agents/passive-claim task_id=<recommended_task_id> lease_id=<lease_id>
  -> ok true
agent performs review-only planning work
POST /api/agents/passive-claim/release result=completed_review summary=<short result>
POST /api/agents/heartbeat-plan status=idle
```

## 7. Recommended maintenance loop

Run periodically from an operator, cron, or future scheduler:

```bash
curl -X POST 'http://localhost:8000/api/agents/passive-claims/expire?limit=50' \
  -u admin:change-me | jq
```

Agents can also inspect active passive claims before attempting new work:

```bash
curl 'http://localhost:8000/api/agents/passive-claims?agent_id=gemini-cli-x1-370' \
  -u admin:change-me | jq
```
