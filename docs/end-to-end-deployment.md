# End-to-end canary deployment

This runbook deploys the current source revision beside the existing AssistX
stack, exercises its graph and control-plane contracts, and writes a durable
evidence bundle. The first pass is observation plus cache cataloging. Migration,
bounded improvement, and signed recovery are added as explicit stages instead
of enabling every mutation authority at once.

## What the bundle deploys

`compose.canary.yml` overrides the normal stack with:

- isolated `assistx-canary-*` container names;
- a dedicated Docker network;
- API binding on loopback port `18000`;
- worker concurrency of one;
- repository task generation, Hermes self-task generation, recovery dispatch,
  and unsafe shell execution disabled by default;
- source-revision labels on every canary service.

The deployment does not stop or replace the current production containers.
Named volumes and Neo4j data are preserved by rollback.

## 1. Prepare configuration

Generate untracked mode-`0600` control-plane and recovery-node files. Existing
core database/model values are inherited from `.env` when present; independent
canary secrets are generated without being printed:

```bash
make canary-init
```

This creates:

```text
deploy/canary.env
deploy/canary-recovery-node.env
```

Review `deploy/canary.env` and replace any remaining database/model
placeholders. The deployment script rejects missing or placeholder values. To
initialize without a source `.env`, or select a different recovery node:

```bash
PYTHONPATH=src python scripts/init-canary-env.py \
  --source /path/to/existing.env \
  --recovery-node-id xwing
```

Do not copy the generated recovery-node file to unrelated nodes. Both generated
files are untracked.

For the initial deployment:

```env
CANARY_STAGES=observe,cache
ASSISTX_RECOVERY_EXECUTION_ENABLED=false
FLEET_RECOVERY_RUNBOOKS_ENABLED=false
FLEET_UNSAFE_SHELL_TASKS_ENABLED=false
CANARY_START_HERMES=false
```

Use `CANARY_MANAGED_NEO4J=false` to validate against the existing AssistX
database. Set it to `true`, change `NEO4J_URI=bolt://neo4j:7687`, and use a
distinct database/volume when an isolated graph is required.

## 2. Host preflight

The host needs:

- Docker Engine with Docker Compose 2.24.4 or newer (the isolated network
  override uses the supported `!override` reset tag);
- `curl`, Git, and Python 3.12;
- access to the configured Neo4j and OpenAI-compatible model endpoint;
- the external `git_default` Docker network when router integration is used;
- a clean checkout at the exact PR revision;
- two healthy registered graph nodes before the migration stage;
- a node agent with a matching node token before the recovery stage.

Render configuration without starting services:

```bash
CANARY_ENV_FILE=deploy/canary.env \
docker compose \
  --env-file deploy/canary.env \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  config
```

## 3. Deploy observation and cache stages

```bash
CANARY_ENV_FILE=deploy/canary.env scripts/deploy-e2e-canary.sh
```

The command:

1. rejects unsafe or placeholder configuration;
2. refuses a dirty source checkout;
3. records the exact Git revision, validated service/image plan, and Compose
   input checksums without rendering secrets into evidence;
4. builds and starts isolated Redis, API, and worker services;
5. waits for production-profile health;
6. validates Redis, Neo4j, runtime configuration, controllers, dashboard,
   migration history, improvement status, and cache status;
7. creates, claims, heartbeats, and completes a graph task;
8. proves a stale claim cannot complete that task;
9. registers a short-lived affinity-only cache manifest;
10. records a cache hit and verifies storage locators are not exposed;
11. captures health, metrics, container state, and bounded logs.

Evidence is written under:

```text
artifacts/deployment-canary/<UTC timestamp>/
```

The pass condition is a zero exit status and `"ok": true` in
`canary-report.json`.

## 4. Add cross-node migration

Confirm both configured nodes are healthy and unblocked in Operations:

```env
CANARY_STAGES=observe,cache,migration
CANARY_MIGRATION_SOURCE=xwing
CANARY_MIGRATION_DESTINATION=x1-370
```

Run the same deployment command again. It creates a harmless preemptible graph
task and drives:

```text
source claim -> RUNNING -> PAUSING -> checkpoint -> PAUSED
-> migration -> destination claim -> stale source rejection -> DONE
```

The stage fails unless migration audit events exist and the old source claim is
fenced.

## 5. Add bounded improvement

Mount the repository and dedicated worktree root into the code-capable worker,
then configure:

```env
CANARY_STAGES=observe,cache,migration,improvement
CANARY_IMPROVEMENT_REPOSITORY=auto-assist
ASSISTX_REPOSITORY_ROOTS_JSON={"auto-assist":"/process-visible/path/auto-assist"}
ASSISTX_IMPROVEMENT_WORKTREE_ROOT=/var/lib/assistx/improvement-worktrees
ASSISTX_IMPROVEMENT_VERIFY_KEYS={"xwing-improvement-v1":"<node-key>"}
```

Without another flag, this stage stops safely at a `PROPOSED` task and verifies
the bounded contract. To execute it after reviewing the proposal:

```env
CANARY_EXECUTE_IMPROVEMENT=true
CANARY_START_HERMES=true
```

The canary may change only
`tests/fixtures/deployment_canary.txt` and must pass
`pytest -q tests/test_deployment_canary.py`. Promotion remains a separate
fingerprint-confirmed operator action and is not performed by the deployment
script. The proposal is targeted to `CANARY_NODE_ID`, preventing another
production code worker from claiming the canary.

## 6. Add signed recovery health check

Enable one non-critical node first. Its local environment must contain:

```env
FLEET_RECOVERY_RUNBOOKS_ENABLED=true
FLEET_RUNBOOK_VERIFY_KEYS={"runbook-canary-v1":"<matching-key>"}
FLEET_NODE_TOKEN=<matching-node-token>
```

Then update the canary control-plane environment:

```env
CANARY_STAGES=observe,cache,migration,recovery
CANARY_RECOVERY_NODE_ID=xwing
ASSISTX_RECOVERY_EXECUTION_ENABLED=true
CANARY_EXECUTE_RECOVERY=true
```

The recovery stage dispatches only the typed `health_check` action. It requires
the signed proposal to reach `DONE` and captures its audit evidence. Do not add
service restart, model reload, or deployment actions to this first end-to-end
run.

After the recovery run, return
`ASSISTX_RECOVERY_EXECUTION_ENABLED=false` unless the operator is beginning the
separate recovery expansion sequence.

## 7. Rollback

```bash
CANARY_ENV_FILE=deploy/canary.env scripts/rollback-e2e-canary.sh
```

Rollback stops and removes only the `assistx-canary-*` containers. It
intentionally preserves named volumes, graph data, and the evidence directory.
Inspect failed logs and any `PROMOTING` or active recovery records before
manually deleting data.

Emergency authority shutdown remains:

```env
ASSISTX_RECOVERY_EXECUTION_ENABLED=false
FLEET_RECOVERY_RUNBOOKS_ENABLED=false
FLEET_UNSAFE_SHELL_TASKS_ENABLED=false
ASSISTX_REPO_TASK_GENERATOR_ENABLED=false
ASSISTX_REPO_TASK_AUTO_READY=false
```

## Ready-for-merge evidence

Attach or retain the evidence directory proving:

- exact source revision, service/image plan, and Compose input checksums;
- production-profile health with Redis and Neo4j available;
- stage-aware readiness with unsafe shell disabled;
- fenced ordinary task completion;
- cache manifest and event lifecycle;
- source-to-destination migration with stale-source rejection;
- bounded improvement proposal, plus verified execution if enabled;
- signed recovery health check if enabled;
- clean rollback that preserved state.

The affinity-only cache stage is sufficient for the initial merge. A real
llama.cpp/SGLang cache-transfer sidecar remains a separate deployment.
