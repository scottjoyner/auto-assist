# Reconciliation deployment package

This directory supports a side-by-side migration while the old stack remains running. It is designed to make preparation repeatable without granting a local agent permission to alter production implicitly.

## Files

- `system-inventory.yaml` — machine-readable systems, services, ports, state, runtime seed, evidence, gates, and rollback policy.
- `migration-state.example.yaml` — operator-owned migration ledger template. It records repository revisions, baseline evidence, production recovery details, shadow isolation, physical runtime ownership, approved LAN/Tailscale paths, model identity, slots, probes, approvals, blockers, cutover readiness, and rollback state.
- `lan-runtime-map.example.json` — example operator mapping from physical node names to preferred RFC1918 inference URLs. Copy it to the ignored `lan-runtime-map.json` and replace every sample value.
- `../reconciliation.env.example` — shadow environment template with isolated ports, database, secrets, shared reconciliation network, and disabled mutation authority.
- `../../compose.reconciliation.yml` — AssistX router-only shadow override.
- `../../scripts/bootstrap-reconciliation-worktrees.sh` — dry-run-first worktree bootstrap across all nine repositories; it never changes production services.
- `../../scripts/reconciliation-preflight.sh` — read-only live baseline collector.
- `../../scripts/reconciliation-discover-tailnet.py` — read-only candidate discovery from `tailscale status --json`; it never admits a node or loads a model.
- `../../scripts/reconciliation-verify-offline.sh` — fail-closed offline verifier.
- `../../scripts/validate-reconciliation-state.py` — validates the ledger for shadow readiness or the stronger operator-approved production cutover gate.
- `../../scripts/render-reconciliation-report.py` — produces a checksum-bearing, non-secret Markdown status report from the ledger.
- `../../docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md` — complete side-by-side execution sequence.
- `../../docs/FINAL_CUTOVER_OPERATOR_PACKET_20260730.md` — final machine-side evidence, approval stop, cutover order, and rollback packet.
- `../../docs/TAILSCALE_RUNTIME_ACCESS_20260730.md` — LAN-first/Tailscale-fallback identity, Docker reachability, and movement behavior.
- `../../docs/LOCAL_AGENT_HANDOFF_20260730.md` — operating contract for a local agent.
- `../../docs/MIGRATION_STATE_LEDGER_20260730.md` — ledger update and validation rules.

The matching `auto-router` branch provides:

- `config/providers.reconciliation.yaml` with one physical runtime and ordered LAN/Tailscale paths;
- `compose.reconciliation.yml` with loopback-only publication and the shared AssistX network;
- a narrowed strict-offline runtime entrypoint;
- per-runtime slot admission and bounded queueing;
- LAN-first access-path selection with Tailscale fallback;
- authenticated `/admin/admission` evidence;
- `scripts/verify_reconciliation_network.py` for read-only container path checks;
- a mounted-runtime test suite and isolated Docker smoke test;
- mandatory strict-offline startup validation.

## Worktree preparation

From any checkout of the reconciliation `auto-assist` branch, inspect the plan:

```bash
make reconciliation-worktrees-plan
```

The dry run reports missing repositories, dirty tracked files, existing worktrees, and branch mismatches. After review:

```bash
make reconciliation-worktrees-apply
```

Missing repositories remain blockers by default. The underlying script supports `--allow-clone`, but cloning should be an explicit operator/local-agent decision because private repositories require working GitHub authentication.

## First migration commands

From the reconciliation `auto-assist` worktree:

```bash
make reconciliation-init
make reconciliation-preflight
```

`reconciliation-init` creates two untracked, mode-0600 files when absent:

```text
deploy/reconciliation.env
deploy/reconciliation/migration-state.yaml
```

Replace every `change-me` value in the environment file. Update the migration ledger only from captured evidence; do not mark an unknown as passed.

The preflight script is read-only. Review its generated evidence before starting shadow services and confirm ports `18000`, `18088`, `17474`, and `17687` are not already in use.

## Private-network inventory

Create the real LAN map, then collect Tailscale candidates:

```bash
cp deploy/reconciliation/lan-runtime-map.example.json \
  deploy/reconciliation/lan-runtime-map.json
$EDITOR deploy/reconciliation/lan-runtime-map.json
sha256sum deploy/reconciliation/lan-runtime-map.json \
  > artifacts/reconciliation-lan-runtime-map.sha256
make reconciliation-discover-tailnet
```

The resulting candidate inventory includes online and off-site peers but remains candidate-only. Each admitted runtime still needs physical process/model identity, explicit capacity, direct/routed canaries, and operator approval.

## State gates

Validate shadow readiness:

```bash
make reconciliation-state-validate
```

This remains blocked until the ledger records, at minimum:

- a reviewed baseline and exact old-stack restart commands;
- no public inference;
- isolated and healthy shadow AssistX/router services;
- passing rendered configuration and offline verification;
- checksum-backed Tailscale candidate inventory and LAN runtime map;
- one physically resolved runtime with both approved LAN and Tailscale paths;
- container-level path reachability, LAN preference, Tailscale fallback, and one shared admission counter;
- direct/routed completion, slot, cancellation, state-authority, restart, and rollback-rehearsal evidence;
- no unresolved blockers.

Validate the stronger cutover gate:

```bash
make reconciliation-cutover-gate
```

That additionally requires a passing Hermes synthetic task, a production Neo4j backup, explicit operator approval, recorded client/old-stack/rollback plans, and `cutover.recommended: true`.

The validator does not perform cutover. A passing ledger is evidence that the operator may review the exact commands; it is not permission for the agent to execute them.

## Status report

Generate the handoff report from the current ledger:

```bash
make reconciliation-report
```

The report is written to `artifacts/reconciliation-report.md` by default and includes the ledger checksum, gate results, repository revisions, private-network evidence, selected runtime paths, admitted and quarantined runtimes, blockers, and approval state. Generated reports and the working ledger are ignored by Git.

Do not run the base Compose file alone for this migration. Use the exact file sequence and approval boundaries in the final operator packet and live migration runbook.
