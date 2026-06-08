# Delegation Gap Closure Plan

> **For Hermes:** Use subagent-driven-development to execute this plan task-by-task once the missing services and contracts are in place.

**Goal:** Stand up real agent-to-agent delegation so AssistX can route, assign, claim, execute, and report work across the fleet instead of relying on the chat session alone.

**Architecture:** AssistX remains the canonical task-state system. auto-router provides placement/routing decisions, auto-assign provides scheduler and lease-aware assignment, and a Hermes adapter claims READY tasks and executes them on eligible agents. Sophia voice events and other intake paths continue to write into AssistX/Neo4j, but delegation becomes an explicit overlay with health, heartbeats, and replayable audit events.

**Tech Stack:** Docker Compose, FastAPI, Neo4j, Redis, Hermes CLI, auto-router, AssistX overlay endpoints, Sophia voice-agent.

---

## Current state

- AssistX API and worker are running.
- Sophia voice-agent is running and webhook auth is now wired correctly.
- auto-router is running.
- The Hermes adapter container is not currently running.
- There is no local auto-assign repository/service checked out yet.
- AssistX docs already describe an overlay mode, but the full router+assign path is not active.

## Missing elements to close the delegation gap

1. A running assignment service (`auto-assign` or equivalent) that can score, lease, and place work.
2. A running Hermes adapter that polls READY tasks and executes them on behalf of AssistX.
3. Explicit overlay configuration in Compose and environment files so the delegation layer is not implicit.
4. A task-claim/heartbeat/lease contract so tasks do not get stuck RUNNING forever.
5. A node registry for fleet agents with capabilities, health, and preferred roles.
6. A smoke test proving READY -> RUNNING -> DONE end-to-end.
7. A clear fallback policy for direct execution when the overlay is disabled or unhealthy.

---

## Phase 1: Confirm the overlay topology

### Task 1: Decide the deployment shape for auto-assign

**Objective:** Determine whether auto-assign will be a new service in the AssistX stack or a separate repo/service that AssistX talks to.

**Files:**
- Read: `/home/scott/git/auto-assist/docs/OVERLAY_INTEGRATION.md`
- Read: `/home/scott/git/auto-assist/README.md`
- Read: `/home/scott/git/auto-router/docker-compose.yml`
- Inspect: local repo layout under `/home/scott/git/` for any existing auto-assign implementation

**Output required:**
- A short decision note: where auto-assign lives, how it is started, and which URLs it exposes.

**Verification:**
- We can point to one canonical `AUTO_ASSIGN_BASE_URL` and one startup command.

### Task 2: Add a concrete overlay configuration profile

**Objective:** Make delegation an explicit runtime mode instead of an ad hoc setup.

**Files:**
- Modify: `/home/scott/git/auto-assist/docker-compose.yml`
- Modify: `/home/scott/git/auto-assist/docs/OVERLAY_INTEGRATION.md`
- Modify: `/home/scott/git/auto-assist/.env.example` if needed

**Work to do:**
- Add `ASSISTX_OVERLAY_MODE=router_plus_assign` example values.
- Add `AUTO_ROUTER_BASE_URL` and `AUTO_ASSIGN_BASE_URL` examples.
- Document the health expectations for each overlay service.

**Verification:**
- `docker compose config` shows the overlay variables.
- `GET /health` reports overlay status separately from core status.

---

## Phase 2: Restore the worker side of delegation

### Task 3: Bring back the Hermes adapter service

**Objective:** Restore the service that polls READY tasks and claims them for execution.

**Files:**
- Modify: `/home/scott/git/auto-assist/docker-compose.yml`
- Inspect/modify: `/home/scott/git/auto-assist/src/assistx/agents/hermes_agent_adapter.py`
- Inspect/modify: `/home/scott/git/auto-assist/src/assistx/agents/hermes_memory_provider.py`

**Work to do:**
- Confirm the adapter’s polling, claiming, and completion reporting path.
- Ensure it uses the current AssistX task schema and Neo4j connection settings.
- Make the adapter visible as a first-class service in Compose.

**Verification:**
- Container starts cleanly.
- Logs show polling for READY tasks.
- A mock or seeded task can be claimed and marked RUNNING.

### Task 4: Add task lease and heartbeat semantics

**Objective:** Prevent dead RUNNING tasks and support recovery if a worker disappears.

**Files:**
- Inspect/modify: `/home/scott/git/auto-assist/src/assistx/neo4j_client.py`
- Inspect/modify: `/home/scott/git/auto-assist/src/assistx/agents/hermes_agent_adapter.py`
- Inspect/modify: `/home/scott/git/auto-assist/src/assistx/overlay.py`
- Add tests under `/home/scott/git/auto-assist/tests/`

**Work to do:**
- Define a lease owner, lease expiry, and heartbeat timestamp.
- Make claim/update operations atomic enough to avoid double-claiming.
- Add reclaim logic for expired leases.

**Verification:**
- A task with an expired lease becomes eligible again.
- Two workers cannot claim the same READY task at the same time.

---

## Phase 3: Add the routing and assignment layer

### Task 5: Stand up auto-router + auto-assign as explicit services

**Objective:** Make route selection and assignment placement happen outside AssistX core.

**Files:**
- Modify: `/home/scott/git/auto-router/docker-compose.yml`
- Add or locate the auto-assign service repository/codebase
- Modify AssistX overlay client code if needed

**Work to do:**
- Confirm auto-router health and routing contract.
- Create or locate auto-assign service implementation.
- Give auto-assign a read-only API over AssistX backlog/context plus write-back event hooks.

**Verification:**
- `auto-router` health is green.
- `auto-assign` health is green.
- A dry-run can produce a placement decision without mutating task state.

### Task 6: Define the agent registry contract

**Objective:** Give the scheduler enough metadata to choose the right worker.

**Files:**
- Modify/add docs in `/home/scott/git/auto-assist/docs/swarm_contracts/`
- Potentially modify `/home/scott/git/auto-assist/src/assistx/paperclip_client.py`
- Potentially modify `/home/scott/git/auto-assist/src/assistx/overlay.py`

**Work to do:**
- Define node identity, roles, capabilities, load, and endpoint fields.
- Decide where registration lives and how heartbeats are persisted.
- Make the registry compatible with delegation decisions.

**Verification:**
- A node can register and heartbeat.
- The scheduler can read the registry and produce a ranked candidate list.

---

## Phase 4: Prove it works end to end

### Task 7: Add a delegation smoke test

**Objective:** Prove that a task can traverse the full delegation lifecycle.

**Files:**
- Add tests under `/home/scott/git/auto-assist/tests/`
- Potentially add a small smoke script under `/home/scott/git/auto-assist/scripts/`

**Scenario:**
1. Create a READY task in Neo4j.
2. Have the adapter claim it.
3. Confirm it moves to RUNNING.
4. Confirm the worker completes it.
5. Confirm the final state is DONE and the result is recorded.

**Verification:**
- The test fails before the overlay is enabled.
- The test passes after the overlay and worker services are enabled.

### Task 8: Add operator-facing status and fallback rules

**Objective:** Make it obvious when delegation is healthy, degraded, or disabled.

**Files:**
- Modify: `/home/scott/git/auto-assist/docs/STATUS.md`
- Modify: `/home/scott/git/auto-assist/docs/PASSIVE_STATUS.md`
- Modify: `/home/scott/git/auto-assist/docs/PASSIVE_CONTROL.md`
- Modify: `/home/scott/git/auto-assist/README.md`

**Work to do:**
- Document direct mode vs router mode vs router_plus_assign mode.
- Document what happens when auto-assign is offline.
- Document the manual fallback path for development.

**Verification:**
- An operator can tell from docs and health output whether the fleet is actually delegating.

---

## Suggested implementation order

1. Decide where auto-assign lives.
2. Restore the Hermes adapter service.
3. Add explicit overlay configuration.
4. Implement leases/heartbeats.
5. Stand up or locate auto-assign.
6. Wire the agent registry.
7. Add the smoke test.
8. Update operator docs and fallback rules.

## First acceptance milestone

The first meaningful milestone is:
- one READY task created in AssistX,
- one worker claims it,
- the task transitions to RUNNING,
- the task finishes DONE,
- and the result is visible in AssistX/Neo4j.

Once that works, we can expand to multi-agent routing and load-aware placement.
