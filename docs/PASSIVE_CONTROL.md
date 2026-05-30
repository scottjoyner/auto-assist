# Passive Agent Global Control

## 1. Purpose

Passive control is the operator-level switchboard for the passive agent system.

It lets an operator control whether passive/background agents should:

- keep accepting new passive work;
- pause immediately;
- drain current work and avoid new work;
- enter maintenance mode while claims/events/status are repaired.

This is separate from individual heartbeats and passive claims. It is a global control surface.

## 2. Endpoints

### Read current control state

```text
GET /api/agents/passive-control
```

Example response:

```json
{
  "ok": true,
  "mode": "enabled",
  "passive_allowed": true,
  "new_claims_allowed": true,
  "renewals_allowed": true,
  "recommended_agent_status": "idle",
  "reason": "default enabled; no PassiveAgentControl node exists yet",
  "updated_by": null,
  "updated_at_ts": null,
  "metadata": {}
}
```

### Set current control state

```text
POST /api/agents/passive-control
```

Request:

```json
{
  "mode": "draining",
  "reason": "User is about to start interactive work; finish checkpoints and stop pulling new passive tasks.",
  "updated_by": "operator",
  "metadata": {
    "source": "dashboard"
  }
}
```

## 3. Modes

| Mode | Meaning | New passive claims | Renewals | Recommended agent status |
|---|---|---:|---:|---|
| `enabled` | Normal passive work allowed | yes | yes | `idle` |
| `paused` | Stop passive/background work | no | no | `paused` |
| `draining` | Finish current checkpoint, no new work | no | yes | `draining` |
| `maintenance` | Operator/system repair mode | no | no | `paused` |

## 4. Intended agent behavior

Agents should check passive control before starting new passive work.

Recommended loop:

```text
GET /api/agents/passive-control
  if mode=enabled:
    call heartbeat-plan and maybe passive-claim
  if mode=draining:
    renew only if needed to finish smallest safe checkpoint, then release
  if mode=paused or maintenance:
    release or stop at safe checkpoint; do not start new work
```

## 5. Current implementation status

Implemented:

- `GET /api/agents/passive-control`
- `POST /api/agents/passive-control`
- `PassiveAgentControl {id:'global'}` Neo4j node
- default enabled state when no control node exists
- recommended agent status per mode
- route registration through `assistx.api_router`
- unit tests for default mode and mode-to-agent-status mapping

Still to finish:

- heartbeat planner should read passive control and force `stay_paused`, `finish_current_step_then_pause`, or normal planning based on mode;
- passive claim creation should be blocked unless `new_claims_allowed=true`;
- passive claim renewal should be blocked unless `renewals_allowed=true`;
- passive status should include the current global control state;
- passive events should record global control changes.

## 6. Safety boundary

Passive control does not execute anything. It only stores a global coordination state. Enforcement is intentionally handled by heartbeat/claim/status flows so agents and operators can see why work is allowed, paused, or draining.
