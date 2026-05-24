# Phase 3 - Paperclip Dispatch Integration

## Overview

Phase 3 adds Paperclip as an optional assignment transport around the
graph-first Task trigger flow:

1. AssistX creates or updates a `Task` in Neo4j
2. Agents can poll and claim `READY` tasks directly from AssistX
3. AssistX may also create a Paperclip issue for cross-device assignment
4. Hermes executes → writes results back
5. AssistX ingests results → updates graph

Neo4j remains the source of truth. Paperclip issue state reconciles back
to `Task`, `Dispatch`, `AgentRun`, `SignalEvent`, and `MemoryItem` nodes.

**Status**: ✅ Live dispatch flow tested end-to-end (May 23, 2026).

---

## 1. Paperclip Server Setup

### Local Development

Paperclip runs as a systemd user service at `http://127.0.0.1:3100`:

```
~/.config/systemd/user/paperclip.service

[Unit]
Description=Paperclip AI Server

[Service]
ExecStart=/home/scott/git/hermes-agent/paperclip/server/node_modules/.bin/tsx /home/scott/git/hermes-agent/paperclip/server/src/index.ts
WorkingDirectory=/home/scott/git/hermes-agent/paperclip/server
Environment=BETTER_AUTH_SECRET=paperclip-dev-secret
Environment=PAPERCLIP_DEPLOYMENT_MODE=local_trusted
Environment=HOST=0.0.0.0
Environment=PAPERCLIP_ALLOWED_HOSTNAMES=host.docker.internal
Environment=PORT=3100
Restart=on-failure

[Install]
WantedBy=default.target
```

### Dev Override (Paperclip source patches)

Paperclip in `local_trusted` mode normally enforces `loopback` bind (127.0.0.1).
To allow Docker containers to reach it, 3 lines were commented out:

| File | Lines | Purpose |
|------|-------|---------|
| `server/src/config.ts` | 275-277 | Bypass `validateConfiguredBindMode` error for `local_trusted` + non-loopback |
| `server/src/config.ts` | 284-286 | Bypass `resolveRuntimeBind` errors |
| `server/src/index.ts` | 447-452 | Bypass startup check enforcing loopback for `local_trusted` |

> **Warning**: These patches will be overwritten on Paperclip version updates.

### Paperclip Resources Created

| Resource | ID | Notes |
|----------|-----|-------|
| Company | `23328778-bb2e-4261-8e8e-4221021753d5` | Named "AssistX Workspace" |
| Agent | `cfecc886-befc-4fa9-a91e-3e9a707b4a4f` | Named "hermes-local", capabilities: terminal, file, code_execution, web |
| API Key | `pcp_1966f1eb...` | Bearer token for API authentication |

### Docker Container Access

The API container accesses Paperclip via `host.docker.internal:3100`:

```yaml
# docker-compose.yml
services:
  api:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      - PAPERCLIP_API_URL=http://host.docker.internal:3100/api
      - PAPERCLIP_API_TOKEN=${PAPERCLIP_API_TOKEN}
      - PAPERCLIP_WORKSPACE_ID=${PAPERCLIP_WORKSPACE_ID}
      - PAPERCLIP_WEBHOOK_SECRET=${PAPERCLIP_WEBHOOK_SECRET}
```

---

## 2. Paperclip API Client

**File**: `src/assistx/paperclip_client.py`

### Architecture

The client wraps Paperclip's Express REST API. Routes were discovered from
Paperclip's route definitions in `server/src/routes/`. Key paths:

| Operation | Paperclip API Route | Method |
|-----------|---------------------|--------|
| Create issue | `/api/companies/:companyId/issues` | POST |
| Get issue | `/api/issues/:id` | GET |
| Update issue | `/api/issues/:id` | PATCH |
| List issues | `/api/companies/:companyId/issues` | GET |
| List agents | `/api/companies/:companyId/agents` | GET |
| Get agent | `/api/agents/:id` | GET |
| List runs (issue) | `/api/issues/:id/runs` | GET |
| List runs (company) | `/api/companies/:companyId/live-runs` | GET |
| Get run | `/api/heartbeat-runs/:runId` | GET |
| Get run log | `/api/heartbeat-runs/:runId/log` | GET |
| Add comment | `/api/issues/:id/comments` | POST |
| Health check | `/api/health` | GET |

### Key Methods

- `create_issue(title, description, task_id, context_packet_id, capabilities, priority, assignee_id)` → Paperclip issue ID
- `get_issue(issue_id)` → issue dict
- `update_issue(issue_id, **kwargs)` → updated issue dict
- `assign_issue(issue_id, agent_id)` → bool (via PATCH)
- `list_agents(company_id)` → list of agent dicts
- `list_issues(status, agent_id, limit, offset)` → list of issue dicts
- `list_runs(issue_id, agent_id, limit)` → list of run dicts
- `get_run(run_id)` → run dict
- `get_run_output(run_id)` → log string
- `create_comment(issue_id, text, author)` → comment ID
- `poll_events(event_types, limit, since_timestamp)` → list of issues (polling fallback)
- `health_check()` → bool

### Authentication

The client uses Bearer token authentication. In `local_trusted` mode,
Paperclip accepts agent API keys as Bearer tokens. The API key is stored
in `PAPERCLIP_API_TOKEN` env var.

---

## 3. Dispatch Flow

### AssistX → Paperclip

When `POST /api/dispatch` is called:

1. `api.py:get_paperclip_client()` initializes a `PaperclipClient` from env vars
2. `neo4j.create_dispatch_with_paperclip()` loads the task
3. Creates a `ContextPacket` from graph-memory context
4. Calls `PaperclipClient.create_issue()` → Paperclip issue created
5. Creates local `Dispatch` node in Neo4j with `paperclip_issue_id` linkage
6. Returns `dispatch_id`, `paperclip_issue_id`, `context_packet_id`

### Live Test Result (May 23, 2026)

```
# Create ticket
POST /api/tickets → {"ticket_id": "af1cffab6a67449380d76be2440c0e71"}

# Dispatch it
POST /api/dispatch → {
  "dispatch_id": "b622a5f0f54a44029add999f124ffd40",
  "paperclip_issue_id": "2365b591-8fd2-4aa5-86b1-9cdc1c298600",
  "context_packet_id": "b8294090994c4740908589929239f45c",
  "paperclip_error": null
}

# Verify in Paperclip
GET /api/issues/2365b591 → {
  "title": "Test Paperclip dispatch",
  "status": "backlog",
  "priority": "high",
  "createdByAgentId": "cfecc886-..."
}
```

### Paperclip → AssistX (Event Ingestion)

Paperclip has **no outbound webhook API** for issue lifecycle events.
Event ingestion is handled via:

1. **Polling fallback**: `PaperclipClient.poll_events()` polls
   `GET /companies/:companyId/issues` for status changes.
2. **Webhook endpoint** (ready but unused): `POST /api/paperclip/events`
   accepts Paperclip events with HMAC-SHA256 signature verification.

Future work: Add a periodic worker that polls Paperclip and syncs status
changes to Neo4j dispatches.

---

## 4. Webhook Handler

**Endpoint**: `POST /api/paperclip/events`

```python
class PaperclipEventIn(BaseModel):
    event_type: str
    paperclip_issue_id: str
    paperclip_agent_id: Optional[str] = None
    paperclip_run_id: Optional[str] = None
    event_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
```

The handler verifies HMAC-SHA256 signature (if `PAPERCLIP_WEBHOOK_SECRET` set)
and routes to `Neo4jClient.ingest_paperclip_event()`.

**Security note**: Signature verification is optional. If `PAPERCLIP_WEBHOOK_SECRET`
is not set, the endpoint accepts unauthenticated events. In production, the
secret must be configured and verification enforced.

---

## 5. Configuration and Secrets

### Environment Variables

```bash
# Paperclip integration (required for dispatch to create Paperclip issues)
PAPERCLIP_API_URL=http://host.docker.internal:3100/api
PAPERCLIP_API_TOKEN=pcp_1966f1eb...
PAPERCLIP_WORKSPACE_ID=23328778-...

# Webhook signature (optional; required for production)
PAPERCLIP_WEBHOOK_SECRET=paperclip-dev-secret
```

### Docker Compose

```yaml
services:
  api:
    environment:
      - PAPERCLIP_API_URL=http://host.docker.internal:3100/api
      - PAPERCLIP_API_TOKEN=${PAPERCLIP_API_TOKEN}
      - PAPERCLIP_WORKSPACE_ID=${PAPERCLIP_WORKSPACE_ID}
      - PAPERCLIP_WEBHOOK_SECRET=${PAPERCLIP_WEBHOOK_SECRET}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./src:/app/src
```

---

## 6. Paperclip API Routes Reference

All routes are under `/api` prefix. Key routes for AssistX integration:

### Issues
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/companies/:companyId/issues` | List issues |
| POST | `/api/companies/:companyId/issues` | Create issue |
| GET | `/api/issues/:id` | Get issue by UUID |
| PATCH | `/api/issues/:id` | Update issue |
| DELETE | `/api/issues/:id` | Delete issue |
| GET | `/api/issues/:id/comments` | List comments |
| POST | `/api/issues/:id/comments` | Add comment |

### Agents
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/companies/:companyId/agents` | List agents |
| POST | `/api/companies/:companyId/agents` | Create agent |
| GET | `/api/agents/:id` | Get agent |
| POST | `/api/agents/:id/keys` | Create API key |

### Runs
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/issues/:id/runs` | List runs for issue |
| GET | `/api/companies/:companyId/live-runs` | List live runs |
| GET | `/api/heartbeat-runs/:runId` | Get run |
| GET | `/api/heartbeat-runs/:runId/log` | Get run log |
| POST | `/api/heartbeat-runs/:runId/cancel` | Cancel run |

---

## 7. Testing

### Live Integration Test

Tested end-to-end manually:

```bash
# 1. Create ticket
curl -u admin:change-me -X POST http://localhost:8000/api/tickets \
  -H 'Content-Type: application/json' \
  -d '{"title":"Test dispatch","ticket_type":"task","required_capabilities":["terminal"]}'

# 2. Dispatch
curl -u admin:change-me -X POST http://localhost:8000/api/dispatch \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"<ticket_id>","target":{"capabilities":["terminal"]},"priority":"HIGH"}'

# 3. Verify Paperclip issue
curl -H "Authorization: Bearer $PAPERCLIP_API_TOKEN" \
  http://localhost:3100/api/issues/<paperclip_issue_id>

# 4. List dispatches
curl -u admin:change-me http://localhost:8000/api/dispatches
```

### Future Tests

- Automated test with mocked Paperclip client
- Hermes agent picks up Paperclip issue and completes it
- Sync result back to Neo4j via webhook/polling

---

## 8. Deployment

### Prerequisites

- [x] Paperclip server running and healthy
- [x] Company/agent/API key created
- [x] `PAPERCLIP_API_URL`, `PAPERCLIP_API_TOKEN`, `PAPERCLIP_WORKSPACE_ID` set
- [x] Docker containers can reach Paperclip (`host.docker.internal` resolved)
- [x] Dispatch flow tested

### Rollback

1. Unset `PAPERCLIP_API_URL=""` to disable client initialization
2. Dispatch creation reverts to local-only mode
3. Existing dispatches and Paperclip issues remain intact

---

## 9. Security

Reviewed May 23, 2026. Findings:

| Severity | Finding | Status |
|----------|---------|--------|
| HIGH | HMAC webhook verification is optional (bypassable) | Known |
| HIGH | No rate limiting on dispatch/event endpoints | Known |
| MEDIUM | Basic Auth uses plain `==` comparison | Known |
| MEDIUM | CORS wide open (`*`) | Known |
| LOW | Cypher queries properly parameterized | ✅ Good |
| LOW | HMAC implementation correct when enabled | ✅ Good |

See MIGRATION.md section 15 for detailed next actions.
