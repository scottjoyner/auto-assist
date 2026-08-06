# AssistX documentation

Start with the current documents below. Files under `archive/`, dated plans,
and the legacy `STATUS.md` are design or deployment records rather than current
operating instructions.

## Current system

| Document | Purpose |
|---|---|
| [`HIGH_LEVEL_DESIGN.md`](HIGH_LEVEL_DESIGN.md) | Canonical system context, authority boundaries, major components, control flows, security, availability, and architectural decisions |
| [`LOW_LEVEL_DESIGN.md`](LOW_LEVEL_DESIGN.md) | Canonical implementation map, graph model, state machines, APIs, fencing, recovery, degraded operation, improvement, configuration, and test contracts |
| [`FLEET_CAPABILITY_ROUTING_DEPLOYMENT.md`](FLEET_CAPABILITY_ROUTING_DEPLOYMENT.md) | Complete Tailscale census, heterogeneous worker roles, LMS benchmark-matrix import, family-specific allocation/routing, scheduled refresh, validation, canaries, and rollback |
| [`SYSTEM_GAP_REVIEW_20260804.md`](SYSTEM_GAP_REVIEW_20260804.md) | Prioritized cross-repository physical, admission, projection, compatibility, recovery, operations, and strategic gaps with acceptance criteria |
| [`FULL_AUTO_RECONCILIATION_20260730.md`](FULL_AUTO_RECONCILIATION_20260730.md) | Authoritative offline-only repository reconciliation, runtime identity contract, containment sequence, and migration gates |
| [`LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md`](LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md) | Detailed side-by-side migration, shadow validation, cutover, and rollback instructions while the old stack remains live |
| [`FINAL_CUTOVER_OPERATOR_PACKET_20260730.md`](FINAL_CUTOVER_OPERATOR_PACKET_20260730.md) | Final machine-side evidence sequence, LAN/Tailscale gates, dependency validation, approval stop, production order, and rollback triggers |
| [`LOCAL_AGENT_HANDOFF_20260730.md`](LOCAL_AGENT_HANDOFF_20260730.md) | Local-agent permissions, prohibitions, workflow, evidence standard, ledger discipline, and completion contract |
| [`MIGRATION_STATE_LEDGER_20260730.md`](MIGRATION_STATE_LEDGER_20260730.md) | Operator-owned evidence ledger, runtime admission records, shadow readiness validation, and production cutover gate |
| [`TAILSCALE_RUNTIME_ACCESS_20260730.md`](TAILSCALE_RUNTIME_ACCESS_20260730.md) | Candidate-only Tailscale discovery, LAN-first path ordering, Docker reachability, movement failover, and cutover evidence |
| [`../deploy/reconciliation/external-dependencies.example.yaml`](../deploy/reconciliation/external-dependencies.example.yaml) | Required service, platform, storage, restore, egress, and executor-containment dependency contract |
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

## Operator interface

The canonical operator UI is `/control-room`.

It replaces the overlapping legacy dashboard pages and provides:

- one row per physical runtime rather than one row per endpoint alias;
- explicit `LM_STUDIO`, `HEADLESS`, or `UNKNOWN` runtime mode;
- loaded model, runtime version, selected LAN/Tailscale path, slots, and queue state;
- human-readable task titles with technical UUIDs available only on expansion;
- measured tokens/second, time-to-first-token, latency, quality, and error evidence;
- live required/optional dependency health;
- server-sent event updates rather than ten-second polling.

Implementation:

- `src/assistx/control_room.py`
- `templates/control_room.html`
- `static/css/control_room.css`
- `static/js/control_room.js`
- `tests/test_control_room.py`

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
| API and authenticated operator routes | `src/assistx/api.py`, `src/assistx/api_router.py` |
| Fleet control room and dependency telemetry | `src/assistx/control_room.py`, `templates/control_room.html`, `static/css/control_room.css`, `static/js/control_room.js` |
| Tailnet node and benchmark-matrix import | `src/assistx/fleet_routing_matrix.py` |
| Complete tailnet context/topology projection | `src/assistx/fleet_context_projection.py` |
| Benchmark-aware heterogeneous allocation | `src/assistx/benchmark_allocation_policy.py`, `src/assistx/allocation_engine.py` |
| Signed runtime/model benchmark hints | `src/assistx/runtime_projection_v2.py` |
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
| External dependency gate | `scripts/validate-external-dependencies.py`, `deploy/reconciliation/external-dependencies.example.yaml` |
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
The reconciliation deployment adds separate dependency, discovery, shadow, and ledger gates:

```bash
make reconciliation-dependencies-validate
make reconciliation-discover-tailnet
make reconciliation-state-validate
make reconciliation-cutover-gate
```

The heterogeneous fleet routing gate is documented in
`FLEET_CAPABILITY_ROUTING_DEPLOYMENT.md`. Its minimum acceptance is a complete
non-admitting tailnet matrix with more than two discovered nodes, observer-only
blocking for unapproved peers, and family-specific routing canaries.

A passing cutover ledger is evidence for operator review, not authorization for a
local agent to modify production.
