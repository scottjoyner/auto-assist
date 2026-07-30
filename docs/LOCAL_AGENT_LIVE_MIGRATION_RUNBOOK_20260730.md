# Local-agent live migration runbook — 2026-07-30

Status: **execution runbook for branch `full-auto-reconciliation-20260730`**

This runbook assumes the old AssistX, router, assignment, and inference stack is
running while the migration is prepared. The migration is therefore staged as:

```text
capture -> isolate -> shadow -> verify -> rehearse rollback -> maintenance cutover
```

The local agent must not convert a shadow deployment into a production cutover
without an explicit operator gate.

## 1. Operating contract for the local agent

The local agent may, without another approval:

- fetch the reconciliation branches;
- create separate Git worktrees;
- inspect files, containers, ports, logs, service status, Tailscale status, and
  LM Studio process inventories;
- run unit tests and configuration rendering;
- create evidence under `artifacts/`;
- start the isolated reconciliation stack on loopback ports `18000`, `18088`,
  `17687`, and `17474`;
- send synthetic, non-sensitive test requests to the shadow stack;
- edit only the reconciliation branches and open or update draft PRs.

The local agent must stop and request operator approval before it:

- stops or restarts any currently running production container or system service;
- changes production ports, DNS, Tailscale names, client base URLs, or reverse proxies;
- loads, unloads, reloads, or moves a model;
- modifies production Neo4j data or restores a database backup;
- enables recovery execution, repository mutation, self-task generation, or
  unrestricted shell execution;
- archives or deletes a repository;
- merges a reconciliation PR;
- enables a quarantined endpoint;
- changes a node from LM Studio to a headless runtime or vice versa.

Non-negotiable rules:

1. The old stack remains available until the maintenance cutover.
2. The shadow stack uses separate container names, loopback ports, network,
   Redis state, Neo4j database/volume, evidence paths, and secrets.
3. `auto-assign` is not started in the shadow stack.
4. Public inference providers and provider keys are absent.
5. Discovery never calls model load or unload.
6. Unknown physical owner or unknown slot capacity means **not admitted**.
7. A successful `/v1/models` response is not proof of physical ownership,
   stability, capacity, or task suitability.
8. Returning a node to LM Studio is acceptable when it improves reliability.

## 2. Required source material

Read these files before running commands:

- `docs/FULL_AUTO_RECONCILIATION_20260730.md`
- `deploy/reconciliation/system-inventory.yaml`
- `docs/end-to-end-deployment.md`
- `docs/EXECUTION_AUTHORITY.md`
- `docs/fleet-recovery-rollout.md`
- `docs/FLEET_OFFLINE_INTEGRATION_20260730.md` in `hermes-agent`
- `docs/reconciliation-20260730.md` in `fleet-llm-profiles`
- `docs/CLI_RECONCILIATION_20260730.md` in `lms`

The inventory file is a seed, not live truth. Replace its assumptions with
observations gathered during this run.

## 3. Required repositories and worktrees

The recommended root is `/home/scott/git`. Do not reuse a dirty production
checkout for migration work.

```bash
export GIT_ROOT=/home/scott/git
export RECON_ROOT=/home/scott/git/reconciliation-20260730
mkdir -p "$RECON_ROOT"
```

For each repository, fetch the branch and create a worktree. The helper below
handles an existing local branch or a first-time tracking branch:

```bash
add_reconciliation_worktree() {
  repo="$1"
  source="$GIT_ROOT/$repo"
  target="$RECON_ROOT/$repo"
  branch=full-auto-reconciliation-20260730

  git -C "$source" fetch origin "$branch"
  if [ -e "$target/.git" ] || [ -f "$target/.git" ]; then
    echo "$target already exists"
    return 0
  fi
  if git -C "$source" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$source" worktree add "$target" "$branch"
  else
    git -C "$source" worktree add -b "$branch" "$target" "origin/$branch"
  fi
}

for repo in \
  auto-assist auto-router auto-assign hermes-agent fleet-llm-profiles \
  fleet-inference-configs fleet-resilience lms ai-research-vault; do
  add_reconciliation_worktree "$repo"
done
```

Verify every worktree:

```bash
for repo in "$RECON_ROOT"/*; do
  [ -e "$repo/.git" ] || continue
  git -C "$repo" status -sb
  git -C "$repo" rev-parse HEAD
done
```

Do not proceed with uncommitted changes unless they are understood and recorded.

## 4. Capture the running baseline

From the reconciliation `auto-assist` worktree:

```bash
cd "$RECON_ROOT/auto-assist"
chmod +x scripts/reconciliation-preflight.sh \
  scripts/reconciliation-verify-offline.sh

export RECONCILIATION_REPO_ROOT="$GIT_ROOT"
export RECONCILIATION_LMS_HOSTS="x1-370,xwing,deathstar-XPS-8920,destroyer,joyner,beelink-ryzen-7-mini-pc"
./scripts/reconciliation-preflight.sh
```

The preflight is intentionally read-only and does not capture container
environment values. Review every generated file under:

```text
artifacts/reconciliation-preflight/<UTC timestamp>/
```

At minimum, resolve and record:

- the actual Compose project names for the live AssistX, router, and auto-assign stacks;
- container names, images/digests, mounts, networks, and published ports;
- the production AssistX and router health URLs;
- the Neo4j deployment type, database name, volume or host path, and backup method;
- the current Git revision for each deployed repository;
- which process owns every listener on ports `1234`, `8000`, `8088`, `8090`,
  `7474`, and `7687`;
- the physical LM Studio host and loaded process for every model currently used;
- any service started by cron, user systemd, system systemd, desktop autostart,
  Docker Compose, or a shell session.

A failed probe means “unknown,” not “absent.”

## 5. Record production restart and rollback commands

Before starting the shadow stack, create an untracked operator file:

```bash
mkdir -p deploy/local
chmod 700 deploy/local
cat > deploy/local/production-stack.env <<'EOF'
OLD_ASSISTX_PROJECT=
OLD_ASSISTX_DIR=
OLD_ASSISTX_ENV_FILE=
OLD_ROUTER_PROJECT=
OLD_ROUTER_DIR=
OLD_ROUTER_ENV_FILE=
OLD_ASSIGN_PROJECT=
OLD_ASSIGN_DIR=
OLD_ASSIGN_ENV_FILE=
OLD_NEO4J_KIND=
OLD_NEO4J_DATABASE=
OLD_NEO4J_VOLUME_OR_PATH=
EOF
chmod 600 deploy/local/production-stack.env
```

Populate it from the baseline. Also save the exact commands that would restart
the old stack. Do not rely on memory during rollback.

Render and retain the current production Compose plans without secrets:

```bash
# Run in each deployed repository using its real files and env file.
docker compose --env-file /path/to/current.env -f docker-compose.yml config \
  > /secure/evidence/current-compose-rendered.yaml
```

Review rendered output before storing it. Redact secret values if Compose has
materialized them.

## 6. Establish a backup strategy before mutation

The shadow phase uses isolated state and does not require a production restore.
A verified production backup is required before cutover.

Acceptable backup methods, in descending preference:

1. a storage-level snapshot of the Neo4j data volume plus its exact image/version;
2. a supported Neo4j Enterprise online backup;
3. a scheduled maintenance stop followed by `neo4j-admin database dump` for the
   exact production database;
4. a tested host-level volume copy while the database is fully stopped.

Do not copy a live Neo4j data directory and call it a backup. Do not stop the
production database during shadow preparation. Record:

- backup command;
- backup timestamp;
- database and Neo4j version;
- checksum;
- restore command;
- restore test result or reason a restore test is pending.

Also preserve production `.env` files, systemd units, cron entries, LM Studio
settings, router/AssistX configuration, and model profile files. Never commit them.

## 7. Prepare the isolated AssistX environment

```bash
cd "$RECON_ROOT/auto-assist"
cp deploy/reconciliation.env.example deploy/reconciliation.env
chmod 600 deploy/reconciliation.env
```

Replace every `change-me` value with independently generated shadow secrets.
Do not copy production runbook, node-token, attestation, or HMAC keys.

Validate that no production port is reused:

```bash
ss -ltnp | grep -E ':(18000|18088|17687|17474)\b' && {
  echo 'A reconciliation port is already in use' >&2
  exit 1
} || true
```

Render the direct observation stack first:

```bash
mkdir -p artifacts/reconciliation-render

docker compose \
  --profile neo4j \
  --env-file deploy/reconciliation.env \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  config > artifacts/reconciliation-render/assistx-direct.yaml
```

Review the render. It must show:

- reconciliation container names;
- loopback API port `18000`;
- isolated network `assistx_reconciliation_default`;
- isolated Neo4j database/volume;
- worker concurrency one;
- recovery, unsafe shell, repository task generation, Hermes self-tasking, and
  auto-assign disabled.

Start only the isolated database, Redis, API, and worker in direct mode:

```bash
docker compose \
  --profile neo4j \
  --env-file deploy/reconciliation.env \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  up -d --build neo4j redis api worker
```

Check status and health:

```bash
docker compose \
  --profile neo4j \
  --env-file deploy/reconciliation.env \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  ps

curl -fsS http://127.0.0.1:18000/health | jq
curl -u "$(grep '^BASIC_AUTH_USER=' deploy/reconciliation.env | cut -d= -f2-):$(grep '^BASIC_AUTH_PASS=' deploy/reconciliation.env | cut -d= -f2-)" \
  http://127.0.0.1:18000/api/fleet/operations-readiness | jq
```

Do not start `hermes-adapter` yet.

## 8. Prepare the isolated strict-offline router

The router branch contains:

- `config/providers.reconciliation.yaml` — one explicitly selected initial
  runtime and all other nodes disabled;
- `compose.reconciliation.yml` — loopback port `18088`, isolated data and Redis,
  strict offline, no autoload, no dispatcher, no agentgateway, no executor mounts.

From the router worktree:

```bash
cd "$RECON_ROOT/auto-router"
mkdir -p data-reconciliation artifacts-reconciliation

docker compose \
  -f docker-compose.yml \
  -f compose.reconciliation.yml \
  config > artifacts-reconciliation/router-rendered.yaml
```

Review the render. It must not contain hosted-provider URLs, API-key variables,
autoload, an agent gateway, production router storage, or the live AssistX
service DNS name.

Start it:

```bash
docker compose \
  -f docker-compose.yml \
  -f compose.reconciliation.yml \
  up -d --build redis llm-router
```

Validate:

```bash
curl -fsS http://127.0.0.1:18088/health | jq
curl -fsS http://127.0.0.1:18088/v1/models | jq
```

## 9. Switch only the shadow AssistX stack to router mode

Return to `auto-assist` and apply the reconciliation override:

```bash
cd "$RECON_ROOT/auto-assist"

docker compose \
  --profile neo4j \
  --env-file deploy/reconciliation.env \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  -f compose.reconciliation.yml \
  config > artifacts/reconciliation-render/assistx-router.yaml

docker compose \
  --profile neo4j \
  --env-file deploy/reconciliation.env \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  -f compose.reconciliation.yml \
  up -d --build --force-recreate api worker
```

Run the offline verifier against both worktrees:

```bash
export RECONCILIATION_NEW_ASSISTX_URL=http://127.0.0.1:18000
export RECONCILIATION_NEW_ROUTER_URL=http://127.0.0.1:18088
./scripts/reconciliation-verify-offline.sh \
  "$RECON_ROOT/auto-assist" \
  "$RECON_ROOT/auto-router/config" \
  "$RECON_ROOT/auto-router/compose.reconciliation.yml"
```

Any forbidden provider, non-loopback shadow control surface, failed health
probe, or non-empty hosted-provider environment variable blocks migration.

## 10. Build the physical runtime inventory

For each physical LM Studio host, use the official LM Studio CLI. The command
named `lms` must belong to LM Studio; the benchmark package uses `lms-agent`.

```bash
command -v lms
lms --help
command -v lms-agent || true
```

Capture loaded processes and installed models per physical host:

```bash
mkdir -p "$RECON_ROOT/auto-assist/artifacts/runtime-inventory"
for host in x1-370 xwing deathstar-XPS-8920 destroyer joyner beelink-ryzen-7-mini-pc; do
  lms ps --json --host "$host" \
    > "$RECON_ROOT/auto-assist/artifacts/runtime-inventory/${host}.ps.json" 2>&1 || true
  lms ls --json --host "$host" \
    > "$RECON_ROOT/auto-assist/artifacts/runtime-inventory/${host}.ls.json" 2>&1 || true
done
```

For every candidate endpoint, record all fields in the canonical runtime
identity contract. In particular:

- observer host;
- access URL;
- transport (`direct`, `lmstudio_link`, or adapter);
- physical runtime host;
- runtime process identity;
- model process identity;
- model key, quantization, context, and runtime version;
- load owner;
- parallel slot count;
- current active and queued requests;
- observation time and expiration;
- direct completion-canary result.

Do not merge duplicate-looking records based only on model name. Do not assign a
Link-exposed model to localhost.

## 11. Admit only one initial runtime

The initial provider should be an operator-confirmed, stable LM Studio runtime,
normally the local x1-370 instance. Before it is enabled:

1. `lms ps --host` resolves the physical process.
2. `/v1/models` lists the expected model.
3. A direct completion returns valid output.
4. Memory use and context settings are known.
5. Slot count is known.
6. The runtime survives repeated sequential calls.
7. The configuration contains no load or unload action.

Use `auto-router/config/providers.reconciliation.yaml`. Keep every other node
disabled until it passes the same gate.

Example direct canary:

```bash
curl -fsS http://127.0.0.1:1234/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"<exact-loaded-model-id>",
    "temperature":0,
    "max_tokens":32,
    "messages":[{"role":"user","content":"Return exactly: RECONCILIATION_OK"}]
  }' | tee artifacts/runtime-inventory/initial-completion.json | jq
```

Then test through the router using the configured alias. Never ask the router to
load the model.

## 12. Shadow validation gates

The local agent must produce evidence for each gate.

### Configuration gate

- all Compose renders reviewed;
- no production port, container name, network, volume, database, or secret reused;
- no hosted-provider URL, key, quota class, or gateway;
- no auto-assign service;
- no autoload or unload controller;
- no Hermes self-task generation;
- recovery and repository mutation disabled.

### Identity gate

- each loaded process appears once;
- physical runtime host is known;
- Link access path and physical owner are distinct fields;
- stale observations expire;
- unresolved owners remain disabled.

### Capacity gate

- every admitted runtime has an explicit slot count;
- unknown capacity is zero;
- a one-slot runtime receives no more than one active generation;
- excess work is queued within a bounded limit or rejected quickly;
- cancellation does not crash the runtime.

If the current router cannot prove this gate, do not cut over. Implement and test
per-runtime admission semaphores first.

### State-authority gate

- AssistX/Neo4j produces the selected node/model decision;
- deleting shadow router SQLite state does not change the next decision after
  observations are rebuilt;
- auto-assign remains stopped;
- Fleet Medic is not running as a recovery authority;
- static profiles create drift findings rather than overwrite observations.

### Restart gate

Restart the shadow router, AssistX API, and worker one at a time. Verify:

- no duplicate runtime/model records;
- no autonomous model load;
- no task claim loss or stale claimant success;
- health returns without manual SQLite repair.

### Rollback gate

Stop and remove only the shadow stack, confirm the production stack never moved,
then recreate the shadow stack from the documented commands. Preserve evidence.

## 13. Synthetic task and Hermes executor gate

Only after all prior shadow gates pass, start the shadow Hermes adapter:

```bash
cd "$RECON_ROOT/auto-assist"
docker compose \
  --profile neo4j \
  --profile executor \
  --env-file deploy/reconciliation.env \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.canary.yml \
  -f compose.reconciliation.yml \
  up -d --build hermes-adapter
```

Restrictions remain:

- one task per loop;
- synthetic task only;
- no production repository target;
- no model loading;
- no self-task generation;
- no recovery execution;
- no promotion, commit, push, or PR creation by the runtime agent.

Use the existing end-to-end canary task lifecycle and retain its report. A
successful chat completion alone is not sufficient; the task must be reserved,
claimed, heartbeated, completed, and fenced against a stale claim.

Stop the shadow adapter after the test unless continued shadow execution is
explicitly approved.

## 14. Headless runtime disposition

Do not mass-convert or mass-revert nodes.

For each node, choose one of:

- `lmstudio_stable` — retain or restore LM Studio;
- `headless_candidate` — preserve but keep disabled pending gates;
- `headless_admitted` — only after identity, completion, benchmark, concurrency,
  supervision, rollback, and capacity gates;
- `quarantined` — reachable but unsafe or unstable;
- `excluded` — too slow, personal, watcher-only, or unsuitable;
- `unknown` — insufficient evidence.

Cron-based process resurrection is not production supervision. A headless
runtime requires a versioned systemd unit, local model/binary path, bounded
restart policy, health check, exact hardware/backend profile, and a tested
rollback to the previous runtime.

Beelink remains quarantined until the documented concurrent-load crash is
resolved and a one-slot canary passes. Xwing remains untouched while its frozen
workload restriction is active. Lenovo is excluded from generation routing on
the documented slow fallback path. The local agent must reverify all statuses.

## 15. Cutover preparation

Cutover requires an explicit maintenance window and operator approval of:

- the final production Compose render;
- production backup and restore evidence;
- admitted runtime list and slot counts;
- client endpoint switch plan;
- exact old-stack restart commands;
- exact rollback threshold and decision owner.

Prepare production configuration with these invariants:

```env
ASSISTX_OVERLAY_MODE=router
AUTO_ROUTER_BASE_URL=http://<private-router-host>:8088
AUTO_ASSIGN_BASE_URL=
HERMES_PROVIDER=assistx-router
HERMES_LM_BASE_URL=http://<private-router-host>:8088/v1
HERMES_SELFTASK_ENABLED=false
ASSISTX_RECOVERY_EXECUTION_ENABLED=false
FLEET_RECOVERY_RUNBOOKS_ENABLED=false
FLEET_UNSAFE_SHELL_TASKS_ENABLED=false
ASSISTX_REPO_TASK_GENERATOR_ENABLED=false
ASSISTX_REPO_TASK_AUTO_READY=false
OPENROUTER_API_KEY=
GROQ_API_KEY=
CEREBRAS_API_KEY=
GEMINI_API_KEY=
MISTRAL_API_KEY=
```

The router production environment must include:

```env
AUTO_ROUTER_STRICT_OFFLINE=true
AUTO_ROUTER_AUTOLOAD_ENABLED=false
AUTO_ROUTER_PLACEMENT_UNLOAD=false
AUTO_ROUTER_FLEET_DISPATCHER_ENABLED=false
AUTO_ROUTER_AGENTGATEWAY_ENABLED=false
```

Keep Hermes disabled for the first production health pass.

## 16. Controlled production cutover

The local agent must print the planned commands and receive approval before
executing this section.

Recommended sequence:

1. announce the maintenance window;
2. stop new external task intake or pause clients;
3. wait for active tasks to complete or checkpoint them;
4. record queue, claims, leases, and running task state;
5. stop the old Hermes/Paperclip/OpenCode consumers;
6. stop the old worker;
7. stop `auto-assign` and disable its restart policy;
8. stop the old router only after clients are paused;
9. stop the old AssistX API;
10. take the verified Neo4j backup or snapshot;
11. render the new production configuration one final time;
12. start Neo4j/Redis if they are part of the new project;
13. start new AssistX API and worker with Hermes disabled;
14. start the strict-offline router;
15. verify health, graph state, runtime identity, and offline policy;
16. send a synthetic completion through the production router;
17. send a synthetic AssistX task through reservation/claim/completion;
18. switch one internal client to the new endpoint;
19. observe errors, queue depth, slot usage, claims, and runtime health;
20. enable the Hermes executor at concurrency one;
21. reopen task intake gradually;
22. keep old configuration and images available until the rollback window closes.

Do not delete old volumes, databases, images, worktrees, configuration, or
containers during cutover. Stopped resources are rollback assets.

## 17. Immediate rollback conditions

Rollback without further optimization when any occurs:

- any public inference provider appears or receives a request;
- a runtime is assigned to the wrong physical host;
- a loaded model is loaded again because of a Link/localhost alias;
- a one-slot endpoint receives concurrent generations;
- task claims or leases are duplicated or lost;
- the new stack cannot rebuild observations after restart;
- production error rate, latency, or queue age breaches the approved threshold;
- a recovery, load, unload, restart, or repository mutation targets the wrong object;
- Neo4j consistency or migration state is uncertain.

Rollback sequence:

1. pause clients and new task intake;
2. stop new Hermes and worker consumers;
3. capture bounded logs and state evidence;
4. stop the new router and AssistX API;
5. restore the previous production environment and Compose files;
6. start the old Neo4j/Redis if they were stopped;
7. start old AssistX API and worker;
8. start old router;
9. start old assignment service only if required by the old deployment;
10. start old executor consumers;
11. verify old health and a synthetic task;
12. reopen clients;
13. preserve the failed new stack state for diagnosis.

Database restoration is a separate operator decision. Do not restore over a
production database merely because the application rolled back.

## 18. Required completion report

The local agent's final migration report must include:

- exact commit SHA of every repository used;
- exact Compose files and rendered-plan checksums;
- live baseline evidence directory;
- old and new container/image inventory;
- old and new port/network/volume inventory;
- Neo4j backup and restore evidence;
- physical runtime and model-process inventory;
- admitted, quarantined, excluded, and unknown nodes with reasons;
- direct and routed completion results;
- one-slot concurrency evidence;
- restart/rebuild evidence;
- synthetic AssistX task lifecycle evidence;
- Hermes executor evidence;
- strict-offline verification output;
- rollback rehearsal result;
- unresolved blockers and operator decisions;
- explicit statement that no public inference provider was configured or called.

A partial result must say exactly which gate failed. The agent must never convert
“could not verify” into “passed.”
