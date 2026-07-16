---
name: assistx
description: "Task authority for the offline swarm: claim tasks, heartbeat, complete work, publish events, and query Neo4j context from the AssistX API"
version: 1.0.0
author: kipnerter
required_environment_variables:
  - name: ASSISTX_API_URL
    prompt: AssistX API base URL (e.g. http://100.64.43.123:8000)
    help: The Tailscale or LAN address of the AssistX control plane
    optional: false
  - name: ASSISTX_AUTH_USER
    prompt: AssistX Basic Auth username
    help: BASIC_AUTH_USER from AssistX .env
    optional: true
  - name: ASSISTX_AUTH_PASS
    prompt: AssistX Basic Auth password
    help: BASIC_AUTH_PASS from AssistX .env
    optional: true
metadata:
  hermes:
    tags: [assistx, swarm, orchestration, tasks, neo4j]
    category: infrastructure
---

# AssistX Skill

## Release Boundary

This direct-worker skill is deferred follow-up work. During the current
Paperclip cutover release, non-realtime production tasks execute through the
registered `hermes_local` Paperclip adapter; do not deploy this skill as a
replacement for that route until cutover is validated and a subsequent
architecture change is approved.

This skill teaches you how to communicate with **AssistX**, the task-state authority for the offline swarm. AssistX owns the authoritative task lifecycle in Neo4j, receives events from Sophia/auto-ingest, and makes work available for workers to claim and execute.

## Architecture

```
You (Hermes agent)
    |
    | curl -> ASSISTX_API_URL
    v
AssistX (task authority, event intake)
    |
    | Neo4j
    v
Tasks, Events, AgentRuns, ToolCalls, Artifacts
```

## Configuration

Set these environment variables before using the skill:

```bash
export ASSISTX_API_URL=http://100.64.43.123:8000     # Tailscale IP of x1-370
export ASSISTX_AUTH_USER=admin                        # BASIC_AUTH_USER
export ASSISTX_AUTH_PASS=change-me                    # BASIC_AUTH_PASS
```

If auth is not set, requests will still work for trusted-network endpoints (Tailscale/LAN).

### Auth Helper

```bash
# Use this pattern for all authenticated requests
_AUTH="-u ${ASSISTX_AUTH_USER}:${ASSISTX_AUTH_PASS}"
```

---

## Task Lifecycle

### 1. Find Work

List READY tasks:

```bash
curl -s $_AUTH "$ASSISTX_API_URL/api/tasks?status=READY" | python3 -m json.tool
```

List READY tasks filtered by capability:

```bash
curl -s $_AUTH "$ASSISTX_API_URL/api/tasks?status=READY&capability=llm" | python3 -m json.tool
```

Get a single READY task:

```bash
curl -s $_AUTH "$ASSISTX_API_URL/api/tasks/ready" | python3 -m json.tool
```

### 2. Claim a Task

Claim a task with a lease. The lease defaults to 900 seconds (15 minutes). Set a custom lease if you expect long-running work:

```bash
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/tasks/{task_id}/claim" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "hermes-kipnerter",
    "capabilities": ["llm", "code", "terminal", "web"],
    "lease_seconds": 900
  }' | python3 -m json.tool
```

On success you get `{"claimed": true, "task": {...}}`. On failure you get `{"claimed": false, "reason": "..."}`.

Important: If a task is already claimed by another node, you will get `"reason": "not_ready"` with the current status. Move on to another task.

### 3. Send Heartbeats

Heartbeats extend your lease. Send them periodically while working:

```bash
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/tasks/{task_id}/heartbeat" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "hermes-kipnerter",
    "status": "RUNNING"
  }' | python3 -m json.tool
```

If you do not heartbeat, the lease expires and the task returns to READY for another node to claim.

### 4. Complete a Task

When the task is done:

```bash
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/tasks/{task_id}/complete" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "hermes-kipnerter",
    "status": "DONE",
    "summary": "Brief summary of what was done",
    "result": {"key": "value"}
  }' | python3 -m json.tool
```

The result should contain the key outputs of the task. Status must be one of: `DONE`, `FAILED`, `CANCELLED`.

### 5. Fail a Task

If something goes wrong:

```bash
# Retryable failure (task returns to READY)
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/tasks/{task_id}/fail" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "hermes-kipnerter",
    "error_summary": "Brief description of what went wrong",
    "retryable": true
  }' | python3 -m json.tool

# Non-retryable failure (task goes to FAILED)
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/tasks/{task_id}/fail" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "hermes-kipnerter",
    "error_summary": "Cannot proceed - dependency missing",
    "retryable": false
  }' | python3 -m json.tool
```

---

## Publishing Events

After completing work, publish an `agent.run.completed` event so the swarm has an audit trail:

```bash
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/events" \
  -H 'Content-Type: application/json' \
  -d '{
    "event_id": "run-UUID",
    "event_type": "agent.run.completed",
    "source_repo": "hermes-agent",
    "source_service": "hermes-kipnerter",
    "node_id": "scotts-macbook-air",
    "occurred_at": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
    "idempotency_key": "run-UUID",
    "schema_version": "1.0",
    "subject": {"kind": "task", "id": "task-{task_id}"},
    "payload": {
      "task_id": "{task_id}",
      "status": "DONE",
      "summary": "Completed the task",
      "agent_id": "hermes-kipnerter"
    },
    "artifact_refs": [],
    "privacy": {
      "pii": false,
      "privacy_class": "private",
      "retention_class": "keep"
    }
  }' | python3 -m json.tool
```

---

## Querying Context from Neo4j

Before working on a task, get context from Neo4j memory:

```bash
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/brain/context" \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "What context is relevant to this task?",
    "max_results": 5
  }' | python3 -m json.tool
```

You can also query memory directly:

```bash
curl -s $_AUTH "$ASSISTX_API_URL/api/memory" | python3 -m json.tool
```

---

## Swarm Node Registration

If this node is not already registered, register it:

```bash
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/swarm/nodes/register" \
  -H 'Content-Type: application/json' \
  -d '{
    "node_id": "scotts-macbook-air",
    "hostname": "kipnerter",
    "status": "online",
    "roles": ["hermes_agent", "model_endpoint"],
    "tailscale_ip": "100.85.64.117",
    "capabilities": [
      {"capability_id": "kipnerter.llm.chat", "kind": "llm", "name": "Qwen 3.5 0.8B local chat"},
      {"capability_id": "kipnerter.code.edit", "kind": "code_edit", "name": "Code editing and generation"},
      {"capability_id": "kipnerter.terminal", "kind": "shell", "name": "Local terminal access"},
      {"capability_id": "kipnerter.hermes", "kind": "hermes_agent", "name": "Hermes agent runtime"}
    ],
    "os": "darwin",
    "arch": "arm64"
  }' | python3 -m json.tool
```

---

## Full Workflow Example

Here is a complete workflow from finding work to publishing results:

```bash
# 1. Find READY tasks
TASKS=$(curl -s $_AUTH "$ASSISTX_API_URL/api/tasks/ready")
TASK_ID=$(echo "$TASKS" | python3 -c "import sys,json; tasks=json.load(sys.stdin); print(tasks[0]['id'] if tasks else '')")

if [ -z "$TASK_ID" ]; then
  echo "No tasks available"
  exit 0
fi

echo "Claiming task: $TASK_ID"

# 2. Claim the task
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/tasks/$TASK_ID/claim" \
  -H 'Content-Type: application/json' \
  -d '{"agent_id": "hermes-kipnerter", "capabilities": ["llm","code","terminal"]}' \
  | python3 -m json.tool

# 3. Work on the task (heartbeat periodically)
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/tasks/$TASK_ID/heartbeat" \
  -H 'Content-Type: application/json' \
  -d '{"agent_id": "hermes-kipnerter", "status": "RUNNING"}' > /dev/null

# ... do the actual work ...

# 4. Complete the task
curl -s -X POST $_AUTH \
  "$ASSISTX_API_URL/api/tasks/$TASK_ID/complete" \
  -H 'Content-Type: application/json' \
  -d '{
    "agent_id": "hermes-kipnerter",
    "status": "DONE",
    "summary": "Task completed successfully"
  }' | python3 -m json.tool
```

---

## Swarm API Reference

### Task API
| Endpoint | Method | Purpose |
|---|---|---|
| `/api/tasks?status=READY` | GET | List available tasks |
| `/api/tasks/{id}` | GET | Get task details |
| `/api/tasks/{id}/claim` | POST | Claim task with lease |
| `/api/tasks/{id}/heartbeat` | POST | Extend lease |
| `/api/tasks/{id}/complete` | POST | Complete task |
| `/api/tasks/{id}/fail` | POST | Fail task |
| `/api/tasks/leases/release-expired` | POST | Release expired leases |

### Swarm API
| Endpoint | Method | Purpose |
|---|---|---|
| `/api/swarm/nodes/register` | POST | Register this node |
| `/api/swarm/nodes/{id}/heartbeat` | POST | Node heartbeat |
| `/api/swarm/nodes` | GET | List registered nodes |
| `/api/swarm/capabilities` | GET | List all capabilities |

### Events
| Endpoint | Method | Purpose |
|---|---|---|
| `/api/events` | POST | Publish event envelope |
| `/api/events` | GET | Query events |

---

## Environment Detection

The following information describes this node for registration and task execution:

| Property | Value |
|---|---|
| node_id | scotts-macbook-air |
| hostname | kipnerter |
| tailscale_ip | 100.85.64.117 |
| os | darwin (macOS) |
| arch | arm64 |
| model | Qwen 3.5 0.8B via LM Studio (127.0.0.1:1234) |
| capabilities | llm, code_edit, terminal, hermes_agent |

Use `uname -a` and `hostname` to confirm if these values are current.

---

## Delegating to opencode-cli (machine-usable results)

Some swarm tasks need a *programmatically consumable* answer, not a prose
summary — e.g. auto-ingest asking for a classification token, auto-assign
asking for a JSON assignment, or auto-router asking for a placement decision.
For those, solve the task by delegating to a real **opencode-cli** session via
Hermes's `delegate_task` tool and a **return contract**:

```python
# in src/assistx/agents/hermes_agent_adapter.py
result = run_hermes_delegated(
    prompt,                                   # the task
    model=model,                              # tool-capable tier model
    provider=HERMES_PROVIDER,
    return_format="verbatim",                 # or "json" / "summary"
)
```

`run_hermes_delegated()` enables the `delegation` toolset and instructs Hermes
to call `delegate_task(provider="opencode-cli", goal=<task>, return_format=...,
role="leaf")` exactly once, then relay the child's result **verbatim**. The
child opencode session returns just the requested value (a token for
`"verbatim"`, a single object for `"json"`) instead of narrative — that value
can then be parsed, compared, or fed to the next tool.

Enable it per tier via env (no code change needed):

```bash
export HERMES_DELEGATE_OPENCODE_TIERS="tool-small"   # tiers that delegate
export HERMES_DELEGATE_RETURN_FORMAT="verbatim"      # verbatim | json | summary
```

When a routed task's tier is in `HERMES_DELEGATE_OPENCODE_TIERS`,
`process_task()` calls `run_hermes_delegated()` instead of a free-form
`hermes chat` session, and the auto-ingest / auto-assign / auto-router
consumers receive the machine-usable result in the task `result.output`.

> Return-contract semantics and the full `delegate_task` wiring live in the
> hermes-agent repo: `AGENTS.md` and `website/docs/guides/delegation-patterns.md`.

---

## File Location

This skill lives at `~/.hermes/skills/assistx/SKILL.md` on the node. To update:

```bash
scp docs/hermes-skill/SKILL.md scottjoyner@100.85.64.117:.hermes/skills/assistx/SKILL.md
```

The Hermes agent auto-discovers skills from `~/.hermes/skills/` at startup. No restart needed — the skill is loaded on-demand when the agent calls `skill_view("assistx")`.
