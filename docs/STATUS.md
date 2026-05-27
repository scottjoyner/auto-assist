# AssistX Status - May 26, 2026

## Current State

AssistX is the task-state authority and Sophia ingestion target. The approved
release path is **Plan B: stabilize and cut over through Paperclip**. Paperclip
runs locally and the `hermes_local` adapter is registered, but cutover has not
passed its completion canary. The direct `hermes-agent-adapter.service` poller
must remain enabled until it does.

### Verified

- Sophia secured enrollment supports one-time verification proofs and an operator override.
- Sophia container configuration targets the shared `assistx` Neo4j database.
- Paperclip is running as a user service and has a registered `hermes_local` adapter.
- A signed Sophia-originated canary created canonical graph/task/dispatch records and Paperclip issue `ASS-14`.
- After the Paperclip wrapper repair, `ASS-14` reached `done` and recorded a completion comment through `hermes_local`.
- AssistX automatic Sophia dispatch now creates Paperclip issues through an initialized client and retains successful run output even after Paperclip clears live run pointers during escalation.
- Paperclip-dispatched tasks are reserved from the legacy direct worker claim path while rollback remains enabled.

### Active Blocker

On May 26, 2026, the repaired signed canary `ASS-14` reached `done`, but its
`hermes_local` run then timed out after 300 seconds rather than exiting
normally. Earlier canaries also demonstrate that missing issue dispositions
fall through to a broken legacy process-backed `hermes-local` recovery agent.
Cutover remains blocked until a canary completes with a successful terminal
run, not merely a `done` issue status.

### Release Architecture

| Component | Release role |
|----------|--------------|
| Sophia | Realtime voice/auth edge; sends signed non-realtime work to AssistX |
| AssistX | Canonical ingestion, graph/task authority, Paperclip dispatch/synchronization |
| Paperclip | Non-realtime execution route for this release |
| `hermes_local` | Supported Paperclip adapter |
| Direct poller | Rollback path; remains enabled until canaries pass |
| Swarm/direct worker claiming | Deferred follow-up; not part of cutover |

### Next Steps

1. Repair Hermes/Paperclip run termination after it writes a valid issue disposition; the wrapper now tells it to end immediately after `done`, pending validation.
2. Repeat the signed-ingest canary and run a non-destructive secured-enrollment authorization/audit canary.
3. Rotate previously committed/local development secrets and keep only environment templates in source control.
4. Only after completion synchronizes successfully, disable `hermes-agent-adapter.service`.

### Deferred Work

- Direct worker claiming and distributed/fleet routing.
- Model endpoint probing as an execution selection mechanism.
- Paperclip deprecation or demotion to an optional mirror.

### Verification

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_paperclip_poller.py tests/test_outbox_client.py tests/test_swarm_phase2.py tests/test_migration_api.py tests/test_paperclip_client.py
```
