# Reconciliation deployment package

This directory supports a side-by-side migration while the old stack remains
running. It is designed to make preparation repeatable without granting a local
agent permission to alter production implicitly.

## Files

- `system-inventory.yaml` — machine-readable systems, services, ports, state,
  runtime seed, evidence, gates, and rollback policy.
- `migration-state.example.yaml` — operator-owned migration ledger template. It
  records repository revisions, baseline evidence, production recovery details,
  shadow isolation, physical runtime ownership, model identity, slots, probes,
  approvals, blockers, cutover readiness, and rollback state.
- `../reconciliation.env.example` — shadow environment template with isolated
  ports, database, secrets, and disabled mutation authority.
- `../../compose.reconciliation.yml` — AssistX router-only shadow override.
- `../../scripts/reconciliation-preflight.sh` — read-only live baseline collector.
- `../../scripts/reconciliation-verify-offline.sh` — fail-closed offline verifier.
- `../../scripts/validate-reconciliation-state.py` — validates the ledger for
  shadow readiness or the stronger operator-approved production cutover gate.
- `../../docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md` — complete execution
  sequence.
- `../../docs/LOCAL_AGENT_HANDOFF_20260730.md` — operating contract for a local agent.

The matching `auto-router` branch provides:

- `config/providers.reconciliation.yaml`;
- `compose.reconciliation.yml`;
- a narrowed strict-offline runtime entrypoint;
- a mounted-runtime test suite and isolated Docker smoke test;
- mandatory strict-offline startup validation.

## First commands

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

Replace every `change-me` value in the environment file. Update the migration
ledger only from captured evidence; do not mark an unknown as passed.

The preflight script is read-only. Review its generated evidence before starting
shadow services and confirm ports `18000`, `18088`, `17474`, and `17687` are not
already in use.

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
- one physically resolved and explicitly admitted runtime;
- direct/routed completion, slot, cancellation, state-authority, restart, and
  rollback-rehearsal evidence;
- no unresolved blockers.

Validate the stronger cutover gate:

```bash
make reconciliation-cutover-gate
```

That additionally requires a passing Hermes synthetic task, a production Neo4j
backup, explicit operator approval, recorded client/old-stack/rollback plans, and
`cutover.recommended: true`.

The validator does not perform cutover. A passing ledger is evidence that the
operator may review the exact commands; it is not permission for the agent to
execute them.

Do not run the base Compose file alone for this migration. Use the exact file
sequence and approval boundaries in the live migration runbook.
