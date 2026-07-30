# AssistX

AssistX is the graph-backed control plane for the offline Hermes fleet. It captures work, evaluates node and model capability, allocates tasks, fences execution, coordinates recovery and migration, and turns bounded code changes into signed, operator-reviewed improvement candidates.

Neo4j is the durable source of truth. Direct Hermes workers are the primary execution integration; OpenCode is a lower-priority explicit executor. The offline `auto-router` may be deployed as the OpenAI-compatible gateway, but it does not own assignment, live inventory, model loading, or worker lifecycle. `auto-assign` is retired in the reconciled deployment.

```text
intake -> task graph -> allocation reservation -> fenced Hermes execution
                                      |                     |
                                      v                     v
                         model/node/capacity score    checkpoint/evidence
                                      |                     |
                                      +------ reconciliation
                                                   |
                                recover / migrate / learn / operator promote
```

## Reconciliation status

Branch `full-auto-reconciliation-20260730` establishes the hard repository and runtime boundaries for the offline fleet:

- AssistX/Neo4j is the sole inventory, scheduling, assignment, claim, lease, health, and recovery authority.
- `auto-router` is an offline-only protocol gateway and admission-control seam.
- `auto-assign` must not run.
- LM Studio Link access URLs are not physical runtime identity.
- Discovery cannot load/unload models.
- Unknown runtime capacity is not routable.
- Hosted inference providers are excluded.

Read [`docs/FULL_AUTO_RECONCILIATION_20260730.md`](docs/FULL_AUTO_RECONCILIATION_20260730.md) before deploying this branch.

## Live migration while the old stack is running

Do not use the normal quick-start commands to replace a running deployment. The reconciliation branch includes a side-by-side package that uses isolated container names, loopback ports, network, Redis, Neo4j state, artifacts, shadow secrets, and an operator-owned evidence ledger:

- [`docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md`](docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md)
- [`docs/LOCAL_AGENT_HANDOFF_20260730.md`](docs/LOCAL_AGENT_HANDOFF_20260730.md)
- [`docs/MIGRATION_STATE_LEDGER_20260730.md`](docs/MIGRATION_STATE_LEDGER_20260730.md)
- [`deploy/reconciliation/system-inventory.yaml`](deploy/reconciliation/system-inventory.yaml)
- [`deploy/reconciliation/migration-state.example.yaml`](deploy/reconciliation/migration-state.example.yaml)
- [`deploy/reconciliation.env.example`](deploy/reconciliation.env.example)
- [`compose.reconciliation.yml`](compose.reconciliation.yml)
- [`scripts/bootstrap-reconciliation-worktrees.sh`](scripts/bootstrap-reconciliation-worktrees.sh)
- [`scripts/reconciliation-preflight.sh`](scripts/reconciliation-preflight.sh)
- [`scripts/reconciliation-verify-offline.sh`](scripts/reconciliation-verify-offline.sh)
- [`scripts/validate-reconciliation-state.py`](scripts/validate-reconciliation-state.py)
- [`scripts/render-reconciliation-report.py`](scripts/render-reconciliation-report.py)

The safe starting sequence is:

```bash
make reconciliation-worktrees-plan
make reconciliation-worktrees-apply
make reconciliation-init
make reconciliation-preflight
```

The worktree command is dry-run-first. The preflight is read-only. Review the baseline before starting any shadow service.

The local agent must maintain `deploy/reconciliation/migration-state.yaml` from captured evidence. Validate shadow readiness with:

```bash
make reconciliation-state-validate
```

Before presenting a production cutover proposal:

```bash
make reconciliation-cutover-gate
make reconciliation-report
```

A passing ledger is evidence for operator review; it does not authorize the local agent to execute cutover. Production cutover remains an explicit operator-gated maintenance action.

## What is implemented

- Canonical task, event, trace, node, model, and artifact state in Neo4j.
- Capability-, health-, load-, latency-, quality-, and cost-aware allocation.
- Reservation and claim fencing so only the selected node can execute work.
- Durable controller leases and checkpoints with stale-leader rejection.
- Typed, signed recovery runbooks with allowlisted node adapters and rollback.
- Preemptible task checkpoints and bounded cross-node migration.
- Bounded self-improvement contracts for small and large agents.
- Per-attempt detached Git worktrees, executor-measured diffs and tests, and HMAC-signed evidence.
- A graph catalog for opaque prompt-prefix KV caches with exact model/quant/runtime compatibility, privacy scopes, TTL, telemetry, and cache-aware allocation.
- Exact-fingerprint operator promotion with base-HEAD fencing, clean-target checks, verification, and automatic patch reversal on failure.
- An authenticated Operations workspace at `/operations` for fleet state, readiness, allocation, recovery, migration, learning, and promotion.

The self-improvement loop cannot approve itself, promote its own patch, commit, push, open a pull request, or use unrestricted shell payloads.

## Normal development quick start

Copy `.env.example` to `.env`, replace every placeholder secret, then start the development stack:

```bash
set -a
source .env
set +a
docker compose -f docker-compose.yml -f compose.override.yml up -d
docker exec -it assistx-api bash -lc "python -m assistx.cli init"
```

For the strict-offline gateway overlay:

```bash
docker compose -f docker-compose.yml -f compose.overlay.yml up -d
```

The overlay defaults to `router`; it no longer injects or requires `auto-assign`.

Verify the API and open the operator workspace:

```bash
curl -u "$BASIC_AUTH_USER:$BASIC_AUTH_PASS" \
  http://localhost:8000/api/fleet/operations-readiness
```

Then visit `http://localhost:8000/operations`.

Production deployments must explicitly configure repository bind mounts, worktree storage, node identities, attestation keys, recovery allowlists, and one execution backend. Inference providers must remain offline-only and resolve to approved physical LAN/Tailscale runtimes.

## Documentation

- [`docs/INDEX.md`](docs/INDEX.md) — authoritative documentation map
- [`docs/FULL_AUTO_RECONCILIATION_20260730.md`](docs/FULL_AUTO_RECONCILIATION_20260730.md) — repository decisions, strict-offline policy, LM Studio Link identity, containment, migration, and gates
- [`docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md`](docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md) — side-by-side live migration, validation, cutover, and rollback
- [`docs/LOCAL_AGENT_HANDOFF_20260730.md`](docs/LOCAL_AGENT_HANDOFF_20260730.md) — local-agent authority and evidence contract
- [`docs/MIGRATION_STATE_LEDGER_20260730.md`](docs/MIGRATION_STATE_LEDGER_20260730.md) — evidence ledger and readiness/cutover validation rules
- [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md) — current capabilities, safety boundaries, and remaining gaps
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components, authority, and state flows
- [`docs/EXECUTION_AUTHORITY.md`](docs/EXECUTION_AUTHORITY.md) — execution and promotion authority boundaries
- [`docs/fleet-recovery-rollout.md`](docs/fleet-recovery-rollout.md) — recovery, reconciliation, checkpoint, and migration rollout
- [`docs/self-improvement-cycle.md`](docs/self-improvement-cycle.md) — bounded learning-loop design
- [`docs/self-improvement-rollout.md`](docs/self-improvement-rollout.md) — deployment, canary, key rotation, rollback, and troubleshooting
- [`docs/kv-cache-control-plane.md`](docs/kv-cache-control-plane.md) — prefix-cache identity, compatibility, routing, runtime adapters, and rollout
- [`docs/end-to-end-deployment.md`](docs/end-to-end-deployment.md) — isolated deployment with staged task, cache, migration, improvement, recovery, evidence, and rollback gates

The dated [`docs/STATUS.md`](docs/STATUS.md) is retained as a historical cutover record. It is not the current capability statement.

## Test

Run the full unit suite:

```bash
PYTHONPATH=src .venv/bin/pytest -q -m "not integration" \
  --ignore=tests/integration
```

Run the real-Neo4j lifecycle canary when the integration service is available:

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/test_recovery_canary.py
```

The canary covers signed recovery, allocation fencing, migration fencing, bounded improvement, repair proposal creation, and promotion serialization.

Tests under `tests/integration/` expect live AssistX and router services and are intentionally excluded from the unit command. The reconciled deployment must not require `auto-assign`.
