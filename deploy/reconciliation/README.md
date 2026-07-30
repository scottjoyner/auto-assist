# Reconciliation deployment package

This directory supports a side-by-side migration while the old stack remains
running.

## Files

- `system-inventory.yaml` — machine-readable systems, services, ports, state,
  runtime seed, evidence, gates, and rollback policy.
- `../reconciliation.env.example` — shadow environment template with isolated
  ports, database, secrets, and disabled mutation authority.
- `../../compose.reconciliation.yml` — AssistX router-only shadow override.
- `../../scripts/reconciliation-preflight.sh` — read-only live baseline collector.
- `../../scripts/reconciliation-verify-offline.sh` — fail-closed offline verifier.
- `../../docs/LOCAL_AGENT_LIVE_MIGRATION_RUNBOOK_20260730.md` — complete execution
  sequence.
- `../../docs/LOCAL_AGENT_HANDOFF_20260730.md` — operating contract for a local agent.

The matching `auto-router` branch provides:

- `config/providers.reconciliation.yaml`;
- `compose.reconciliation.yml`;
- mandatory strict-offline startup validation.

## First commands

```bash
cp deploy/reconciliation.env.example deploy/reconciliation.env
chmod 600 deploy/reconciliation.env
chmod +x scripts/reconciliation-preflight.sh scripts/reconciliation-verify-offline.sh
./scripts/reconciliation-preflight.sh
```

Do not start services until the generated baseline is reviewed and reconciliation
ports are confirmed unused. Do not run the base Compose file alone for this
migration; use the exact file sequence in the live migration runbook.
