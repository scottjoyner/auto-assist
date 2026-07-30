# Local agent handoff — full auto reconciliation

Use this document as the task contract for a local coding/operations agent with
access to the fleet host, Docker, Tailscale, LM Studio CLI, and the repositories.

## Mission

Prepare and validate the `full-auto-reconciliation-20260730` migration without
interrupting the currently running production stack. Build a side-by-side,
strict-offline AssistX and auto-router shadow deployment, inventory physical
runtime ownership, prove identity and capacity behavior, exercise one synthetic
Hermes task, rehearse rollback, and produce the evidence needed for an operator
to approve a later maintenance cutover.

## Read first

1. `docs/FULL_AUTO_RECONCILIATION_20260730.md`
2. `docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md`
3. `deploy/reconciliation/system-inventory.yaml`
4. `deploy/reconciliation/migration-state.example.yaml`
5. `docs/end-to-end-deployment.md`
6. repository-specific reconciliation documents on the matching branch

## Branch and scope

Work only on:

```text
full-auto-reconciliation-20260730
```

Repositories in scope:

```text
auto-assist
auto-router
auto-assign
hermes-agent
fleet-llm-profiles
fleet-inference-configs
fleet-resilience
lms
ai-research-vault
```

Create separate worktrees. Do not make changes in a dirty live checkout.

## Architecture that must be preserved

```text
Hermes / OpenCode / workloads
            |
            v
strict-offline auto-router
            |
            v
AssistX + Neo4j authority
            |
            v
LM Studio / LM Studio Link / admitted headless runtimes
```

AssistX is the only inventory, allocation, assignment, reservation, claim,
lease, heartbeat, reconciliation, and recovery authority. `auto-assign` must not
run. Auto-router is a gateway and admission-control seam, not a scheduler or
model-placement authority.

The reconciled router runtime must not mount autonomous backlog scheduling,
Tailnet/service/CLI discovery, mutable live-model registry endpoints, model
placement, in-process agent execution, or `/jobs/agent`.

## Actions allowed before operator approval

- inspect and document the live stack;
- run read-only probes;
- create evidence and checksums;
- update the untracked migration ledger from evidence;
- edit reconciliation branches;
- run tests and linters;
- start isolated shadow services on loopback ports;
- send synthetic non-sensitive traffic to shadow services;
- stop and recreate only reconciliation containers;
- open or update draft PRs.

## Actions forbidden before operator approval

- stop, restart, reconfigure, or replace a live production service;
- change production client endpoints, DNS, ports, networks, or volumes;
- write to production Neo4j from the shadow deployment;
- load, unload, reload, or migrate a model;
- enable auto-router autoload or placement reconciliation;
- enable AssistX recovery execution or unrestricted shell tasks;
- allow Hermes self-task generation;
- enable a quarantined node;
- archive/delete repositories or merge PRs;
- expose a public inference provider or provider key;
- convert a passing migration ledger into permission to execute cutover.

## Required migration ledger

Initialize the operator-owned ledger before work begins:

```bash
cd /home/scott/git/reconciliation-20260730/auto-assist
make reconciliation-init
```

This creates, when absent:

```text
deploy/reconciliation.env
deploy/reconciliation/migration-state.yaml
```

Both remain untracked and mode `0600`. Update the ledger after every evidence-
producing step. Each `pass` must be backed by a path, checksum, command output,
health response, benchmark, graph query, or operator approval. Unknown, blocked,
or inaccessible items remain explicit.

Validate the current shadow state with:

```bash
make reconciliation-state-validate
```

Before presenting a cutover proposal, run:

```bash
make reconciliation-cutover-gate
```

A passing cutover gate means the exact plan is ready for operator review. It does
not authorize the local agent to execute production changes.

## Required workflow

### A. Baseline

Run `scripts/reconciliation-preflight.sh`. Resolve every unknown production
project, container, listener, runtime process, model process, and persistence
path. Record exact old-stack restart commands. Enter the evidence directory and
checksums into `migration-state.yaml`.

### B. Isolated control plane

Create `deploy/reconciliation.env` from the example with new shadow secrets.
Start isolated Neo4j, Redis, AssistX API, and worker using the canary and
reconciliation Compose files. Do not start Hermes. Record rendered-plan
checksums, isolation properties, and health evidence in the ledger.

### C. Isolated router

Start auto-router with its reconciliation Compose override and provider file.
It must use loopback port `18088`, its own Redis/data, strict-offline validation,
no public keys, no agentgateway, no autoload, no unload, and no fleet dispatcher.
Its OpenAPI surface must omit the retired scheduler/discovery/executor routes.

### D. Offline and identity verification

Run `scripts/reconciliation-verify-offline.sh`. Inventory each physical LM
Studio host with official `lms ps --json --host <host>`. Preserve observer URL,
Link transport, physical runtime owner, process identity, model identity,
quantization, context, slot count, health, load owner, and freshness separately.
Create one ledger runtime entry per physical loaded model instance.

### E. Minimal admission

Admit one stable operator-confirmed runtime only. Keep Beelink, Lenovo, Optiplex,
Joyner, personal nodes, frozen nodes, and unknown endpoints disabled until their
specific gates pass. An admitted ledger record requires a fresh physical owner,
model process identity, explicit slot count, completion/stability/concurrency/
cancellation probes, and no quarantine reason.

### F. Capacity and restart tests

Prove one-slot protection, bounded queue/rejection behavior, cancellation safety,
router restart, AssistX restart, observation rebuild, stale-state expiry, and no
autonomous model loading. Record evidence and update the corresponding checks.

### G. Shadow readiness gate

Run:

```bash
make reconciliation-state-validate
```

Do not start Hermes while this is blocked. Resolve every reported ledger error or
record it under `blockers` and return `STATUS: BLOCKED`.

### H. Synthetic executor test

After every prior gate passes and the operator approves the shadow executor,
start the reconciliation Hermes adapter with the `executor` profile at one task
per loop. Run one synthetic AssistX task through reservation, claim, heartbeat,
completion, and stale-claim rejection. Stop the adapter afterward unless the
operator approves continued shadow use.

### I. Rollback rehearsal

Remove only reconciliation services and recreate them from the runbook. Confirm
that the production stack did not change. Retain logs, state summaries, and
checksums. Set `rollback.rehearsed` only after the evidence is reviewed.

### J. Cutover proposal

Do not execute production cutover. Produce the exact command sequence,
maintenance duration dependencies, state backup plan, client switch plan,
rollback commands, thresholds, and remaining blockers for operator approval.
Then run:

```bash
make reconciliation-cutover-gate
```

A blocked result is the correct outcome until every operator and production-state
gate is recorded.

## Implementation priorities discovered during migration

Fix these on the reconciliation branches when evidence confirms the need:

1. physical runtime/model-instance schema and observation ingestion in AssistX;
2. official LM Studio `ps --host` collector;
3. per-runtime semaphore and zero-capacity default in auto-router;
4. signed AssistX routing decision consumption by auto-router;
5. no-autoload startup and request-time invariants;
6. profile drift reporting without overwriting observations;
7. Fleet Medic probe absorption into AssistX;
8. migration of proven headless units into `fleet-llm-profiles` with rollback;
9. removal of independent AI Research Vault endpoint discovery;
10. deletion or extraction of retired router scheduler/discovery modules after
    their unique fixtures have moved to AssistX.

## Evidence standard

Every pass/fail statement must cite a command output, test result, health response,
log segment, graph query, checksum, or captured configuration. Never treat an
unreachable host, empty response, missing permission, or failed command as proof
of absence.

Do not capture or publish secrets, raw private prompts, private keys, full Docker
environment arrays, or production data.

## Completion format

Return:

```text
STATUS: PASS | BLOCKED | ROLLED_BACK
PRODUCTION_CHANGED: yes | no
PUBLIC_INFERENCE_FOUND: yes | no
SHADOW_STACK_HEALTHY: yes | no
RUNTIME_IDENTITY_GATE: pass | fail
CAPACITY_GATE: pass | fail
STATE_AUTHORITY_GATE: pass | fail
HERMES_SYNTHETIC_GATE: pass | fail | not_run
ROLLBACK_REHEARSAL: pass | fail
STATE_LEDGER_VALIDATION: pass | fail
CUTOVER_GATE_VALIDATION: pass | fail | not_run
CUTOVER_RECOMMENDED: yes | no
```

Then include:

- repository SHAs;
- migration ledger path and checksum;
- evidence paths;
- inventory changes;
- tests run and failures;
- admitted/quarantined/excluded nodes;
- exact blockers;
- exact operator approvals needed next;
- exact rollback command set.

The correct outcome can be `BLOCKED`. Never make the live system less reliable
merely to complete the migration checklist.
