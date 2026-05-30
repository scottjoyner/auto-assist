# Passive Agent Coordination Status

## 1. Purpose

Passive status gives operators and agents a single read model for the passive-work system.

Instead of checking global control, heartbeats, active claims, stale claims, and idle work separately, callers can use one endpoint to understand whether agents should keep working, renew claims, expire stale work, drain, pause, or wait.

## 2. Endpoints

### Combined passive status

```text
GET /api/agents/passive-status
GET /api/agents/passive-status?agent_id=gemini-cli-x1-370&include_idle_work=true&limit=25
```

Returns:

- global passive control state;
- recent agent heartbeats;
- heartbeat summary counts;
- active and expired passive claims;
- claim summary counts;
- passive-safe idle work candidates when global control allows new claims;
- system recommendations.

Example response shape:

```json
{
  "ok": true,
  "agent_id": null,
  "control": {
    "mode": "enabled",
    "passive_allowed": true,
    "new_claims_allowed": true,
    "renewals_allowed": true,
    "recommended_agent_status": "idle"
  },
  "heartbeat_summary": {
    "total": 2,
    "idle": 1,
    "busy": 1
  },
  "claim_summary": {
    "total": 1,
    "active": 1,
    "expired": 0,
    "review_only": 1,
    "claim_ready": 0
  },
  "idle_work_count": 3,
  "recommendations": [
    {
      "level": "info",
      "action": "heartbeat_idle_agents",
      "reason": "idle agents and passive-safe work are available"
    },
    {
      "level": "info",
      "action": "monitor_claim_renewals",
      "reason": "busy agents should renew or release passive claims before TTL expiry"
    }
  ],
  "read_only": true
}
```

### Passive maintenance

```text
POST /api/agents/passive-maintenance?limit=50
```

Runs passive claim expiry cleanup and then returns a fresh passive status snapshot.

This endpoint is safe maintenance only. It does not execute tasks, dispatch workers, or write repo files.

## 3. Recommendation actions

| Action | Meaning |
|---|---|
| `keep_agents_paused` | Global control is `paused` or `maintenance`; agents should not start or renew passive work |
| `drain_current_work` | Global control is `draining`; agents should finish safe checkpoints and avoid new claims |
| `expire_stale_claims` | Expired passive claims are present; run maintenance cleanup |
| `heartbeat_idle_agents` | Idle agents and safe passive work exist; agents should request a heartbeat plan |
| `monitor_claim_renewals` | Busy agents should renew or release before TTL expiry |
| `idle_wait` | No passive-safe work and no active claims exist |

## 4. Control-aware behavior

`/api/agents/passive-status` suppresses idle-work suggestions unless global control allows new passive claims.

| Control mode | Idle work returned | Recommendation focus |
|---|---:|---|
| `enabled` | yes | normal heartbeat/renew/release loop |
| `draining` | no | finish safe checkpoints; renew only if needed |
| `paused` | no | keep agents paused |
| `maintenance` | no | keep agents paused while operator repairs state |

## 5. Recommended operator loop

```bash
curl 'http://localhost:8000/api/agents/passive-status' -u admin:change-me | jq
curl -X POST 'http://localhost:8000/api/agents/passive-maintenance?limit=50' -u admin:change-me | jq
```

## 6. Recommended agent loop

Before starting new passive work, an agent may check:

```bash
curl 'http://localhost:8000/api/agents/passive-status?agent_id=gemini-cli-x1-370' \
  -u admin:change-me | jq
```

Then:

1. If recommendation is `keep_agents_paused`, stop at a safe checkpoint and do not renew or claim.
2. If recommendation is `drain_current_work`, finish the smallest safe checkpoint, renew only if needed, then release.
3. If recommendation is `expire_stale_claims`, wait or ask operator maintenance to run.
4. If `heartbeat_idle_agents`, call `POST /api/agents/heartbeat-plan`.
5. If `monitor_claim_renewals`, renew or release active claim.
6. If `idle_wait`, sleep until the next heartbeat interval.

## 7. Boundary

Passive status is a coordination surface. It does not:

- claim tasks;
- renew claims;
- execute work;
- dispatch Paperclip/Hermes;
- write files;
- commit or push.

`POST /api/agents/passive-maintenance` only expires stale passive claims and returns them to `READY`/previous status.
