# Fleet Recovery Rollout

Typed recovery is disabled until both the control plane and each node have an
identity, signing keys, and explicit mutation allowlists.

## Identities and signing keys

Generate independent random secrets for the runbook signer and every node.
Store them in the normal secret manager; do not commit them.

Control plane:

```env
ASSISTX_RUNBOOK_SIGNING_KEYS={"runbook-2026-01":"<signing-secret>"}
ASSISTX_RUNBOOK_ACTIVE_KEY_ID=runbook-2026-01
ASSISTX_FLEET_NODE_TOKENS={"x1-370":"<x1-secret>","xwing":"<xwing-secret>"}
```

Each node receives only its identity token and verification set:

```env
FLEET_NODE_ID=x1-370
FLEET_NODE_TOKEN=<x1-secret>
FLEET_RUNBOOK_VERIFY_KEYS={"runbook-2026-01":"<signing-secret>"}
```

Rotate keys by adding the replacement key everywhere, changing the active key,
waiting longer than the maximum 30-minute attestation lifetime, and removing
the old key.

## Node adapters

Linux:

```env
FLEET_RECOVERY_SERVICE_ALIASES={"inference":{"adapter":"systemd","unit":"lm-studio.service"}}
```

macOS:

```env
FLEET_RECOVERY_SERVICE_ALIASES={"inference":{"adapter":"launchd","label":"com.example.inference"}}
```

Observation-only node:

```env
FLEET_RECOVERY_SERVICE_ALIASES={"inference":{"adapter":"observation","instructions":"Restart inference manually"}}
```

Docker Compose projects must be explicitly mapped:

```env
FLEET_RECOVERY_COMPOSE_PROJECTS={"assistx":"/srv/assistx"}
```

Compose services used for deployment must reference an image environment
variable, such as `image: ${ASSISTX_API_IMAGE}`. Recovery requests must provide
an immutable image reference containing `@sha256:`.

## Canary sequence

Enable one non-critical node first:

```env
FLEET_RECOVERY_RUNBOOKS_ENABLED=true
```

Keep control-plane dispatch disabled. Confirm Operations shows `recovery
ready`, then run a health-only proposal and inspect its signature, steps,
verification, and audit transitions.

Next enable dispatch:

```env
ASSISTX_RECOVERY_EXECUTION_ENABLED=true
```

Test in order:

1. Healthy-service short-circuit.
2. Service restart and verification.
3. Drain, failed verification, and automatic resume.
4. Model reload.
5. Immutable Compose deployment and captured-image rollback.

Expand one node at a time. Do not enable automatic quarantine until recovery
success and rollback rates are stable.

## Emergency shutdown

```env
ASSISTX_RECOVERY_EXECUTION_ENABLED=false
FLEET_RECOVERY_RUNBOOKS_ENABLED=false
FLEET_UNSAFE_SHELL_TASKS_ENABLED=false
```

Expired and stuck proposals reconcile automatically. Inspect drained nodes and
failed rollback evidence before re-enabling automation.

## Operator controls and evidence

- `/api/fleet/operations-readiness` reports required gates, key IDs, node
  identities, and allowlists without returning secret values.
- Maintenance and quarantine controls require a reason and expiry. Expired
  controls are released automatically and every transition creates a
  `FleetControlEvent`.
- Allocation reservations can be released before claim; release clears the
  task target and records the releasing actor.
- Recovery evidence bundles are downloadable from Operations and include the
  proposal, execution evidence, and complete audit transition list.
