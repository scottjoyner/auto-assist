# Passive Agent Heartbeat Planner

## 1. Purpose

AssistX now exposes a passive heartbeat planner so agents can ask what they should do when idle without automatically claiming or executing work.

This solves the workflow gap where agents need to:

1. pause passive/background work when the user asks for something interactive;
2. preserve current focus while busy;
3. resume safe backlog work when idle again;
4. avoid claiming/mutating tasks until an approved claim or dispatch path is used.

## 2. Endpoints

### Heartbeat plan

```text
POST /api/agents/heartbeat-plan
```

Request:

```json
{
  "agent_id": "gemini-cli-x1-370",
  "status": "idle",
  "capabilities": ["code", "terminal", "docs"],
  "current_task_id": null,
  "current_focus": null,
  "max_suggestions": 3,
  "mode": "passive",
  "metadata": {
    "node_id": "x1-370",
    "source": "agent-loop"
  }
}
```

Statuses:

| Status | Behavior |
|---|---|
| `idle` | Suggest safe passive work if available |
| `busy` | Continue current work and do not pull passive backlog |
| `paused` | Stay paused until operator/user resumes |
| `interrupted` | Pause current work, preserve resume info, prioritize user work |
| `offline` | Standby/no work |

Modes:

| Mode | Behavior |
|---|---|
| `passive` | Recommend review-only next candidate |
| `review_only` | Same posture as passive; no claim/execution |
| `claim_ready` | Recommend that caller may claim via approved claim endpoint, but this endpoint still does not claim |

Response:

```json
{
  "agent_id": "gemini-cli-x1-370",
  "status": "idle",
  "received_at_ts": 1760000000000,
  "mode": "passive",
  "current_task_id": null,
  "plan": {
    "action": "review_next_candidate",
    "reason": "eligible passive work found; review-only/dry-run recommendation",
    "recommended_task_id": "task-123"
  },
  "suggestions": [],
  "read_only": true,
  "mutations": ["AgentHeartbeat upsert only"]
}
```

### Idle work candidates

```text
GET /api/agents/idle-work?limit=5&capabilities=code&capabilities=terminal
```

Returns safe passive candidates only. It does not claim or mutate tasks.

## 3. Safety behavior

The passive planner rejects candidates that are:

- sensitive;
- local-only;
- interactive;
- critical;
- not `READY` or `REVIEW`;
- missing required capabilities.

The planner can write/update only an `AgentHeartbeat` node so AssistX can see current agent status. It does not mutate `Task` state.

## 4. Intended loop

A passive agent loop should behave like this:

```text
while running:
  if user-interactive request arrives:
    POST heartbeat-plan status=interrupted current_task_id=<background-task>
    handle user request
    POST heartbeat-plan status=idle current_task_id=<background-task>
    resume or pick recommended passive work
  elif working:
    POST heartbeat-plan status=busy current_task_id=<task>
    continue
  else:
    POST heartbeat-plan status=idle
    review suggested candidate
```

## 5. Current boundaries

Implemented:

- `POST /api/agents/heartbeat-plan`
- `GET /api/agents/idle-work`
- passive safe candidate filtering
- heartbeat state write to `AgentHeartbeat`
- plan actions for idle/busy/paused/interrupted/offline

Not implemented yet:

- automatic task claim;
- execution dispatch;
- worktree sandbox;
- auto-router feedback loop from selected backlog jobs;
- operator approval queue for passive work promotion.

## 6. Recommended next step

Wire agent CLIs or node agents to call `heartbeat-plan` every 30–60 seconds and whenever the user interrupts. Agents should treat returned suggestions as review-only until a later claim/approval flow is implemented.
