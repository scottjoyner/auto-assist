# Messaging Trigger Fundamental Changes - Plan

## Problem Statement

The current messaging trigger chain has a critical blocker: the Paperclip `hermes_local` adapter run **times out after 300 seconds** instead of exiting normally when a canary reaches `done` status. This blocks the entire cutover validation path.

The trigger chain is:
```
Incoming event (voice/Sophia/Paperclip)
  -> EventEnvelope ingestion (POST /api/events or /api/voice/events)
  -> Intent classification (intent_orchestrator.py, scheduled every 15s)
  -> Task creation (neo4j_client.create_task_with_context)
  -> Dispatch selection (auto-select device via Paperclip or direct worker)
  -> Execution via chosen path
  -> Result capture and sync back to AssistX
```

## Current Trigger Paths (3 paths)

### Path A: Paperclip Cutover (BLOCKED)
Sophia -> AssistX -> Paperclip issue -> hermes_local adapter -> Hermes Agent -> result via Paperclip webhook -> AssistX sync

**Blocker**: hermes_local run times out after 300s. The canary wrapper was repaired to terminate immediately after `done`, but the run still stalls.

### Path B: Direct Worker Claim (ROLLBACK ENABLED)
Sophia -> AssistX -> Task (READY) -> hermes-agent-adapter.service polls /api/agent/tasks -> claims task -> runs hermes chat -> completes task

**Status**: Poller enabled as rollback. Works but bypasses Paperclip's issue tracking.

### Path C: Direct API Trigger (IMMEDIATE)
Sophia -> AssistX -> Task (READY) -> intent_orchestrator -> _handle_task -> create_task_with_context(auto_dispatch=False) -> hermes_agent_adapter polls -> claims & runs

## Fundamental Changes Needed

### Change 1: Fix the hermes_local timeout (Path A)

**Root cause analysis needed:**
- The hermes_local adapter is a Paperclip-registered adapter that runs `hermes chat -q "<prompt>"` as a subprocess
- HERMES_TASK_TIMEOUT=300 seconds is the hard limit
- The canary wrapper was supposed to tell the run to end immediately after `done`
- But the run still times out

**Two approaches:**

**Option A1: Increase timeout and diagnose the wrapper**
- The actual work might take longer than 300s. The wrapper's "end immediately after done" instruction might not be reaching the subprocess correctly.
- Need to check: does the wrapper set a signal/flag that the hermes chat process respects?
- Check if the wrapper's disposition is being interpreted by Paperclip correctly.

**Option A2: Fix the wrapper disposition**
- The wrapper likely writes a disposition file or sends a signal to Paperclip to mark the run as complete
- The timeout might be Paperclip's internal watchdog, not the subprocess timeout
- Need to verify the disposition is being sent before Paperclip's watchdog fires

### Change 2: Unify the trigger dispatch mechanism

**Current problem:** Two separate dispatch paths exist:
1. Paperclip dispatch (via PaperclipClient.create_issue)
2. Direct worker claim (via /api/tasks/{id}/claim)

**Fix:** Make `create_task_with_context` accept a `dispatch_mode` parameter:
- `dispatch_mode="paperclip"` -> create Paperclip issue (current)
- `dispatch_mode="direct"` -> set task to READY, let poller pick it up
- `dispatch_mode="auto"` -> auto-select (current default, but should prefer direct for local work)

### Change 3: Add a local-first trigger path

**The fundamental pivot:** For local work (this machine), skip Paperclip entirely and use direct task claim:

```
Intent -> create_task_with_context(dispatch_mode="direct") 
  -> Task.status = "READY" 
  -> hermes_agent_adapter polls and claims
  -> Runs hermes chat locally
  -> Updates task status via /api/tasks/{id}/complete
```

**Why this matters:**
- No Paperclip dependency for local execution
- No 300s timeout from Paperclip's watchdog
- Simpler, more reliable trigger chain
- Can validate the core intent->task->execute->complete loop independently

### Change 4: Add trigger validation canary

**New canary that doesn't depend on Paperclip:**

1. Create a test task with `dispatch_mode="direct"`
2. Verify hermes_agent_adapter picks it up within N seconds
3. Verify the task completes with status "DONE"
4. Verify the output is captured in Neo4j
5. This validates the core trigger loop without Paperclip

### Change 5: Rotate secrets

**Current defaults that need rotation:**
- `NEO4J_PASSWORD=knowledge_graph_2026` in .env
- `BASIC_AUTH_PASS=change-me` in .env
- `PAPERCLIP_API_TOKEN` is committed to .env (should be gitignored)
- `VOICE_WEBHOOK_SECRET` and `PAPERCLIP_WEBHOOK_SECRET` are committed

## Implementation Order

### Phase 1: Validate core trigger loop (unblock immediate work)
1. Write trigger validation canary (Change 4)
2. Add `dispatch_mode` parameter to create_task_with_context (Change 2)
3. Ensure hermes_agent_adapter can claim and complete direct tasks
4. Run canary: create task -> poller picks it up -> completes -> verify
5. If this works, we have a working trigger path independent of Paperclip

### Phase 2: Fix Paperclip timeout (restore full path)
1. Diagnose hermes_local wrapper disposition (Change 1)
2. Fix wrapper to send proper completion signal
3. Re-run original canary ASS-14
4. If passes, disable direct poller rollback

### Phase 3: Unify dispatch (long-term)
1. Implement dispatch_mode parameter everywhere
2. Add auto-select logic (local vs remote)
3. Deprecate Paperclip cutover path if direct worker is sufficient
4. Update swarm contracts

## Validation Commands

After Phase 1:
```bash
# Run core trigger canary
PYTHONPATH=src .venv/bin/python -c "
from assistx.neo4j_client import Neo4jClient
neo = Neo4jClient()
# Create a direct task
task = neo.create_task_with_context(
    title='trigger-test',
    task_type='task',
    kind='trigger_test',
    required_capabilities=['terminal'],
    payload={'test': 'trigger_validation'},
    context_query='Test trigger',
    context_sources=['memory'],
    auto_dispatch=False,  # <-- direct mode
)
print(f'Created task: {task}')
neo.close()
"

# Check hermes_agent_adapter picks it up
# Verify task status transitions: READY -> CLAIMED -> RUNNING -> DONE
```

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| hermes_agent_adapter can't claim direct tasks | High | Test claim endpoint first |
| Direct poller misses tasks | Medium | Verify poll interval and task status filtering |
| Paperclip path remains broken | Medium | Phase 2 fix; Phase 1 doesn't depend on it |
| Secret rotation breaks running services | Medium | Update .env before restart, verify Neo4j auth |

## Decision Point: Pivot or Fix?

**Pivot to direct trigger** is the lower-risk path because:
1. The code already exists (hermes_agent_adapter.py is fully implemented)
2. Tests already pass (46/46)
3. No external dependencies (Paperclip, hermes_local adapter)
4. Can validate the core loop in hours, not days

**Fix Paperclip timeout** is necessary because:
1. Paperclip provides issue tracking and audit trail
2. Sophia cutover requires Paperclip path
3. Fleet routing depends on Paperclip as execution route

**Recommendation:** Do Phase 1 (pivot to direct) first to unblock work, then Phase 2 (fix Paperclip) in parallel.
