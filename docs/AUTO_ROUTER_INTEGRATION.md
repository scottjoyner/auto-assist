# Auto-Router Integration

## 1. Purpose

AssistX exposes read-only endpoints for `auto-router` so the router can consume graph-backed context, evaluate backlog candidates in dry-run mode, and post durable provenance events back to AssistX.

The integration keeps ownership boundaries clear:

- AssistX owns canonical task/context state in Neo4j.
- auto-router owns routing, quota, provider selection, service scanning, and dry-run selection.
- auto-assign consumes the same AssistX state for assignment/scheduler decisions without taking over canonical ownership.
- Paperclip/Hermes remains the approved execution path during the current cutover.

## 2. Entrypoint

The production Docker image now starts:

```text
uvicorn assistx.api_router:app --host 0.0.0.0 --port 8000
```

`assistx.api_router` imports the existing `assistx.api:app` and then includes the router integration endpoints. The large existing API module is not rewritten.

## 3. Endpoints

### Health

```text
GET /health
```

Existing AssistX health endpoint.

### Event sink

```text
POST /api/events
```

Existing event sink route. A `GET /api/events` returning `405` is acceptable and means the path exists but is POST-only.

### Router integration status

```text
GET /api/router/status
```

Returns the endpoints auto-router should use and a compact graph summary.

### Context projection

```text
GET /api/router/context-projection
```

Returns AssistX-backed router context:

- `revision`
- `source`
- `generated_at`
- `nodes`
- `providers`
- `services`
- `metadata`

This is consumed by auto-router through:

```text
AUTO_ROUTER_CONTEXT_CONFIG=http://assistx:8000/api/router/context-projection
```

or, from another container on the same Docker network:

```text
AUTO_ROUTER_CONTEXT_CONFIG=http://172.20.0.5:8000/api/router/context-projection
```

### Backlog candidates

```text
GET /api/router/backlog-candidates?limit=25&queue=backlog&dry_run=true
```

Returns read-only candidate tasks for auto-router dry-run scheduling.

Important: this endpoint does **not** claim, mutate, dispatch, or execute tasks.

It returns:

```json
{
  "tasks": [],
  "count": 0,
  "queue": "backlog",
  "dry_run": true,
  "read_only": true
}
```

Each task is normalized to the shape expected by auto-router:

- `task_id`
- `title`
- `prompt`
- `model`
- `priority`
- `queue_class`
- `local_only`
- `allow_cloud`
- `sensitive`
- `max_completion_tokens`
- `status`
- `queue`
- `privacy`
- `metadata.request` for shared request context
- `metadata.task` for shared task context
- `metadata`

### Priority and queue contract

The backlog endpoint is intentionally conservative. Use these priority classes when preparing tasks for the router:

| AssistX priority | Router priority | Queue expectation |
| --- | --- | --- |
| `low`, `deferred`, `idle` | `background` | Eligible for backlog burn-down |
| `normal`, `medium` | `batch` | Eligible for backlog burn-down |
| `high`, `urgent` | `critical` | Do not send to backlog burn-down |
| `critical` | `critical` | Immediate/priority lane, not backlog |
| `repo_critical` | `repo_critical` | Repo-critical lane, not backlog |
| `interactive`, `realtime` | `interactive` | Interactive lane, not backlog |
| `local_only`, `local` | `local_only` | Local execution only |

Only `batch` and `background` work should appear in `/api/router/backlog-candidates` for dry-run selection. High-priority or interactive work should be queued through the immediate router lanes instead of the backlog burn path.

## 4. Safety behavior

The backlog candidate endpoint preserves privacy and safety fields:

| AssistX task signal | Normalized behavior |
|---|---|
| `privacy=private` | `local_only=true`, `sensitive=true`, `allow_cloud=false` |
| `privacy=secret` | `local_only=true`, `sensitive=true`, `allow_cloud=false` |
| `privacy=voice_auth` | `sensitive=true` |
| `privacy=enrollment_sample` | `sensitive=true` |
| `payload.local_only=true` | `allow_cloud=false` |

Auto-router then performs an additional dry-run policy check before selecting/skipping candidates.

## 5. Context projection contents

The projection includes AssistX-owned nodes and services:

- AssistX API
- AssistX health
- AssistX event sink
- AssistX router context projection
- AssistX backlog candidates
- Neo4j Bolt
- Redis
- Paperclip API

It also includes provider/lane records for:

- AssistX
- Paperclip
- local LM Studio fallback lane
- Cerebras flash-start lane

The Cerebras record is advisory. auto-router still owns actual provider API keys, quota policy, and cloud-use permission checks.

## 6. Validation commands

From the auto-router container or another container on the Docker network:

```bash
curl http://172.20.0.5:8000/health
curl http://172.20.0.5:8000/api/router/status | jq
curl http://172.20.0.5:8000/api/router/context-projection | jq
curl 'http://172.20.0.5:8000/api/router/backlog-candidates?limit=5&queue=backlog&dry_run=true' | jq
```

Expected after this integration:

- `/health` returns `200`.
- `/api/events` returns `405` for GET but accepts POST payloads.
- `/api/router/context-projection` returns `200`.
- `/api/router/backlog-candidates` returns `200`.

## 7. Next steps

1. Add Neo4j merge handlers for auto-router events posted to `/api/events`, including `router.route_decision` and `router.execution_stage.*`.
2. Persist remote service and CLI self-report data into Neo4j.
3. Add AssistX task claim/approval flow after auto-router dry-run selection.
4. Keep Paperclip/Hermes as the approved execution path until a separate worker execution release is approved.
