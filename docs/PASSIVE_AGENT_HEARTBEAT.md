# Passive Agent Heartbeat Planner

## 1. Purpose

AssistX exposes a passive heartbeat planner so agents can ask what they should do when idle without automatically claiming or executing work.

This is the coordination layer for agents that should keep making progress on safe background work, but immediately put that work aside when the user asks for something interactive.

The desired behavior is:

1. agents do useful review/planning work when idle;
2. agents report `busy` while focused;
3. agents report `interrupted` or `user_active=true` when user work preempts them;
4. AssistX returns resume instructions instead of losing context;
5. agents resume current passive work before pulling new work;
6. no task is claimed, executed, committed, or pushed until an approved flow exists.

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
  "last_completed_task_id": null,
  "last_result_summary": null,
  "max_suggestions": 3,
  "mode": "passive",
  "user_active": false,
  "allow_resume_current": true,
  "max_work_seconds": 900,
  "interrupt_policy": "pause_and_resume",
  "metadata": {
    "node_id": "x1-370",
    "source": "agent-loop"
  }
}
```

Statuses:

| Status | Behavior |
|---|---|
| `idle` | Resume current passive work first, otherwise suggest safe passive work |
| `busy` | Continue current work and do not pull passive backlog |
| `draining` | Finish the smallest safe checkpoint, then pause |
| `paused` | Stay paused until operator/user resumes |
| `interrupted` | Pause current work, preserve resume info, prioritize user work |
| `offline` | Standby/no work |

Modes:

| Mode | Behavior |
|---|---|
| `passive` | Recommend review-only next candidate |
| `review_only` | Same posture as passive; no claim/execution |
| `claim_ready` | Recommend that caller may claim via approved claim endpoint, but this endpoint still does not claim |

Important request controls:

| Field | Meaning |
|---|---|
| `user_active` | Forces passive work to yield to interactive/user work |
| `allow_resume_current` | Resume `current_task_id` before picking new work |
| `max_work_seconds` | Advisory lease duration; clamped between 60 and 7200 seconds |
| `interrupt_policy` | Agent-side interruption behavior: `pause_and_resume`, `finish_current_step`, or `stop_now` |

Response:

```json
{
  "agent_id": "gemini-cli-x1-370",
  "status": "idle",
  "received_at_ts": 1760000000000,
  "mode": "passive",
  "current_task_id": null,
  "current_focus": null,
  "user_active": false,
  "plan": {
    "plan_id": "uuid",
    "generated_at_ts": 1760000000000,
    "next_heartbeat_seconds": 45,
    "interrupt_policy": "pause_and_resume",
    "mode": "passive",
    "action": "review_next_candidate",
    "reason": "eligible passive work found; review-only/dry-run recommendation",
    "recommended_task_id": "task-123",
    "lease": {
      "lease_id": "uuid",
      "task_id": "task-123",
      "mode": "passive",
      "lease_type": "advisory_only",
      "issued_at_ts": 1760000000000,
      "expires_at_ts": 1760000900000,
      "max_work_seconds": 900,
      "claim_required_before_execution": true
    },
    "safety": {
      "passive_safe": true,
      "requires_claim_before_execution": true,
      "write_allowed": false
    }
  },
  "suggestions": [],
  "read_only": true,
  "mutations": ["AgentHeartbeat upsert only"],
  "contract": {
    "claiming": "not_performed",
    "execution": "not_performed",
    "task_mutation": "not_performed",
    "lease_type": "advisory_only",
    "operator_approval_required_for_write": true
  }
}
```

### Idle work candidates

```text
GET /api/agents/idle-work?limit=5&capabilities=code&capabilities=terminal
```

Returns safe passive candidates only. It does not claim or mutate tasks.

### Heartbeat summary

```text
GET /api/agents/heartbeat-summary?limit=25
```

Returns recent `AgentHeartbeat` nodes and counts by status. This helps AssistX and operators see which agents are idle, busy, paused, draining, or interrupted.

## 3. Planner actions

| Action | Meaning |
|---|---|
| `review_next_candidate` | Start review-only/dry-run passive work |
| `resume_current` | Resume the existing passive task before pulling a new one |
| `continue_current` | Stay on current task because the agent is busy |
| `yield_to_user` | User-active work is present; pause passive work |
| `pause_current_and_wait_for_user_work` | Agent explicitly reported interruption |
| `finish_current_step_then_pause` | Draining mode; checkpoint and stop |
| `stay_paused` | Operator/local policy pause |
| `idle_wait` | No safe passive work available |
| `standby` | Agent is offline |
| `recommend_claim_via_approved_endpoint` | A candidate exists, but actual claiming must happen elsewhere |

## 4. Advisory leases

A heartbeat plan may include an advisory lease:

```json
{
  "lease_type": "advisory_only",
  "task_id": "task-123",
  "max_work_seconds": 900,
  "claim_required_before_execution": true
}
```

This is **not** a task claim. It is a coordination hint so multiple agents can behave predictably later, and so an agent knows how long to work before checking back in.

Current semantics:

- advisory only;
- does not mutate `Task`;
- does not reserve execution;
- does not grant write permission;
- requires future approved claim endpoint before execution.

## 5. Candidate ranking

Passive candidates are ranked by:

1. priority: `HIGH`, `MEDIUM`, `LOW`;
2. queue class: `batch`, `backlog`, `background`, `docs`;
3. status: `REVIEW` gets a slight preference over `READY`;
4. age: older tasks receive a small boost;
5. required capabilities: more specialized tasks get a small penalty.

Each suggestion includes:

- `rank_score`;
- `why` explanation;
- `safety` block.

## 6. Safety behavior

The passive planner rejects candidates that are:

- sensitive;
- local-only;
- interactive;
- critical;
- not `READY` or `REVIEW`;
- missing required capabilities.

The planner can write/update only an `AgentHeartbeat` node so AssistX can see current agent status. It does not mutate `Task` state.

## 7. Intended loop

A passive agent loop should behave like this:

```text
while running:
  if user-interactive request arrives:
    POST heartbeat-plan status=interrupted current_task_id=<background-task> user_active=true
    handle user request
    POST heartbeat-plan status=idle current_task_id=<background-task> user_active=false
    follow resume_current or review_next_candidate
  elif working:
    POST heartbeat-plan status=busy current_task_id=<task>
    continue until next_heartbeat_seconds or safe checkpoint
  elif finishing before pause:
    POST heartbeat-plan status=draining current_task_id=<task>
    finish smallest safe checkpoint, then pause
  else:
    POST heartbeat-plan status=idle
    review suggested candidate
```

The agent should treat `next_heartbeat_seconds` as the maximum interval before checking back in.

## 8. Current boundaries

Implemented:

- `POST /api/agents/heartbeat-plan`
- `GET /api/agents/idle-work`
- `GET /api/agents/heartbeat-summary`
- passive safe candidate filtering
- ranked candidate suggestions
- advisory leases
- resume-first planning
- user-active yielding
- draining mode
- heartbeat state write to `AgentHeartbeat`
- plan actions for idle/busy/paused/interrupted/offline/draining

Not implemented yet:

- automatic task claim;
- execution dispatch;
- worktree sandbox;
- auto-router feedback loop from selected backlog jobs;
- operator approval queue for passive work promotion;
- real lease conflict prevention across multiple agents.

## 9. Recommended next step

Wire agent CLIs or node agents to call `heartbeat-plan` every 30–60 seconds and whenever the user interrupts. Agents should treat returned suggestions as review-only until a later claim/approval flow is implemented.

The next product slice should be an approved claim endpoint that converts an advisory lease into a real task claim after policy, queue, and operator rules pass.
