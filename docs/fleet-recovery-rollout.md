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

## Durable controller leadership

Recovery reconciliation uses a Neo4j-backed `ControllerLease` and
`ControllerCheckpoint`. Every leadership transfer increments a fencing token.
A controller must still own the unexpired lease with the same token when it
commits a tick result; work produced by a stale leader is rejected.

Multiple API replicas may run the reconciler. Non-leaders remain in standby,
completed tick keys are replay-safe, and failed ticks remain retryable with
their bounded error result recorded in the checkpoint.

The default controller instance identity contains the hostname, process ID, and
a random boot suffix. Set `ASSISTX_CONTROLLER_INSTANCE_ID` only when the
deployment platform supplies a unique replica identity. Never configure the
same fixed identity on multiple live replicas.

Controller ownership, lease expiry, fencing token, attempt count, and last tick
result are available from `GET /api/fleet/controllers` and the Operations
workspace.

## Checkpoint, preemption, and migration

Tasks are non-preemptible unless their producer explicitly sets
`preemptible=true`. Preemptible execution uses the claim ID as an execution
fence:

1. An operator or allocator requests preemption with
   `POST /api/tasks/{id}/preempt`.
2. The current node observes `PAUSING` through its fenced heartbeat.
3. The node writes a versioned checkpoint with
   `POST /api/tasks/{id}/checkpoint` and releases ownership.
4. The durable `execution-reconciler` validates the selected destination and
   returns the task to `READY` with that node as its target.
5. The destination claims a new execution attempt and resumes from
   `checkpoint_json`.

Old claim IDs cannot heartbeat, checkpoint, or complete the migrated execution.
Migration requires a healthy, unblocked `SwarmNode`, and each task has a bounded
`max_migrations` budget. A `PAUSING` task that is not acknowledged within the
timeout returns to its prior execution when the lease is still valid, or to
`READY` after the lease expires.

Benchmark work checkpoints between cases. LLM work can checkpoint before
inference or preserve a completed response so migration does not repeat a paid
or expensive generation. Legacy shell handlers remain non-preemptible and
disabled by default.

Migration history is stored as `TaskMigrationEvent` records and exposed through
`GET /api/fleet/migrations`. The Operations workspace shows progress,
checkpoint revision, migration budget, and preempt/migrate controls.

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
