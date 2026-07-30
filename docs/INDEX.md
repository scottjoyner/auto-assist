# AssistX documentation

Start with the current documents below. Files under `archive/`, dated plans,
and the legacy `STATUS.md` are design or deployment records rather than current
operating instructions.

## Current system

| Document | Purpose |
|---|---|
| [`FULL_AUTO_RECONCILIATION_20260730.md`](FULL_AUTO_RECONCILIATION_20260730.md) | Authoritative offline-only repository reconciliation, runtime identity contract, containment sequence, and migration gates |
| [`LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md`](LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md) | Detailed side-by-side migration, shadow validation, cutover, and rollback instructions while the old stack remains live |
| [`FINAL_CUTOVER_OPERATOR_PACKET_20260730.md`](FINAL_CUTOVER_OPERATOR_PACKET_20260730.md) | Final machine-side evidence sequence, LAN/Tailscale gates, approval stop, production order, and rollback triggers |
| [`LOCAL_AGENT_HANDOFF_20260730.md`](LOCAL_AGENT_HANDOFF_20260730.md) | Local-agent permissions, prohibitions, workflow, evidence standard, ledger discipline, and completion contract |
| [`MIGRATION_STATE_LEDGER_20260730.md`](MIGRATION_STATE_LEDGER_20260730.md) | Operator-owned evidence ledger, runtime admission records, shadow readiness validation, and production cutover gate |
| [`TAILSCALE_RUNTIME_ACCESS_20260730.md`](TAILSCALE_RUNTIME_ACCESS_20260730.md) | Candidate-only Tailscale discovery, LAN-first path ordering, Docker reachability, movement failover, and cutover evidence |
| [`../deploy/reconciliation/system-inventory.yaml`](../deploy/reconciliation/system-inventory.yaml) | Machine-readable repository, service, port, state, runtime, evidence, gate, and rollback inventory |
| [`../deploy/reconciliation/migration-state.example.yaml`](../deploy/reconciliation/migration-state.example.yaml) | Working migration-state template for revisions, evidence, runtime identities, checks, approvals, blockers, and rollback |
| [`../deploy/reconciliation/README.md`](../deploy/reconciliation/README.md) | Reconciliation deployment package entry point |
| [`CURRENT_STATUS.md`](CURRENT_STATUS.md) | Implemented capabilities, verified boundaries, and remaining gaps |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System components, authority, control loops, and state flows |
| [`EXECUTION_AUTHORITY.md`](EXECUTION_AUTHORITY.md) | Which actor may claim, execute, recover, approve, and promote |
| [`fleet-recovery-rollout.md`](fleet-recovery-rollout.md) | Recovery keys, adapters, controller fencing, migration, canary, and shutdown |
| [`self-improvement-cycle.md`](self-improvement-cycle.md) | Design and invariants of evidence-gated repository improvement |
| [`self-improvement-rollout.md`](self-improvement-rollout.md) | Deployment, canary, key rotation, rollback, and troubleshooting |
| [`kv-cache-control-plane.md`](kv-cache-control-plane.md) | Opaque prefix identity, model/quant compatibility, cache-aware allocation, node adapters, security, and rollout |
| [`end-to-end-deployment.md`](end-to-end-deployment.md) | Isolated branch-image deployment, staged canaries, evidence capture, and rollback |
| [`swarm_contracts/`](swarm_contracts/) | Shared event and worker contract reference |

## Historical records and plans

- [`STATUS.md`](STATUS.md) is the June 2026 Paperclip cutover record. It is
  preserved for incident and migration context, not as the current status.
- [`plans/`](plans/) contains dated proposals and handoffs. A plan is not an
  enabled runtime feature unless a current document and code path say so.
- [`archive/`](archive/) contains superseded phase plans, handoffs, and build
  summaries.

## Primary implementation map

| Area | Source |
|---|---|
| API and authenticated Operations routes | `src/assistx/api.py` |
| Operations UI | `templates/operations.html`, `static/operations.js` |
| Neo4j schema, task state, reservations | `src/assistx/neo4j_client.py` |
| Allocation scoring and opportunity cost | `src/assistx/allocation_engine.py` |
| Durable controller leases and fencing | `src/assistx/controller_runtime.py` |
| Checkpoint, preemption, migration | `src/assistx/execution_control.py` |
| Diagnosis and recovery policy | `src/assistx/diagnosis_engine.py`, `src/assistx/recovery_control.py` |
| Typed node runbook execution | `src/assistx/recovery_executor.py` |
| Improvement contracts and learning | `src/assistx/improvement_cycle.py` |
| Isolated worktrees and promotion | `src/assistx/improvement_runtime.py` |
| KV-cache identity, compatibility, and economics | `src/assistx/kv_cache.py` |
| Hermes worker integration | `src/assistx/agents/hermes_agent_adapter.py` |
| Readiness gates | `src/assistx/operations_readiness.py` |
| Live migration scripts | `scripts/reconciliation-preflight.sh`, `scripts/reconciliation-verify-offline.sh` |
| Tailscale candidate discovery | `scripts/reconciliation-discover-tailnet.py` |
| Migration ledger validator | `scripts/validate-reconciliation-state.py` |
| Live migration Compose | `compose.canary.yml`, `compose.reconciliation.yml` |
| Migration state and desired inventory | `deploy/reconciliation/migration-state.example.yaml`, `deploy/reconciliation/system-inventory.yaml` |
| LAN path hints | `deploy/reconciliation/lan-runtime-map.example.json` |

## Verification

```bash
PYTHONPATH=src .venv/bin/pytest -q -m "not integration" \
  --ignore=tests/integration
PYTHONPATH=src .venv/bin/pytest -q tests/test_recovery_canary.py
```

The second command requires the real Neo4j integration environment used by CI.
The reconciliation deployment adds separate discovery, shadow verification, and ledger gates:

```bash
make reconciliation-discover-tailnet
make reconciliation-state-validate
make reconciliation-cutover-gate
```

A passing cutover ledger is evidence for operator review, not authorization for a
local agent to modify production.
