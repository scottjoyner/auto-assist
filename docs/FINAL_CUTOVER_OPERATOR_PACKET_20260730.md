# Final cutover operator packet — 2026-07-30

This is the final machine-side sequence for moving from the existing local fleet stack to the reconciled AssistX/auto-router/Hermes stack.

This packet does **not** authorize production changes. Complete every shadow, backup, rollback, evidence, and CI gate first. Stop before section 18 and present the completed evidence for explicit operator approval.

## 1. Non-negotiable authority model

```text
AssistX / Neo4j
  = durable physical-node, runtime, model, access-path, capacity,
    assignment, claim, lease, health, approval, and recovery authority

auto-router
  = strict-offline OpenAI-compatible gateway, semantic auto/* policy,
    per-runtime admission, bounded queueing, and approved path selection

Hermes
  = claimed-task executor in fleet.mode=external
```

The reconciled deployment must not contain:

- `auto-assign`;
- a Hermes `fleet.nodes` registry;
- `hermes fleet serve` or independent endpoint discovery;
- router-created agent jobs or Paperclip inference routing;
- public providers or hosted credentials;
- autonomous model load, unload, restart, migration, or recovery;
- duplicate slot, health, or access-path authorities.

## 2. Required repository branches and green CI

All production candidates must be clean and pinned to full 40-character SHAs.

```text
auto-assist:  full-auto-reconciliation-20260730
auto-router:  full-auto-reconciliation-20260730
hermes-agent: full-auto-reconciliation-20260730
```

The Hermes reconciliation branch contains the tested external/standalone/disabled fleet split from PR #10 plus the reconciliation contract from PR #11. PRs remain draft until operator review.

For each worktree:

```bash
git fetch origin
git switch full-auto-reconciliation-20260730
git pull --ff-only origin full-auto-reconciliation-20260730
git status --short
git rev-parse HEAD
```

Record the exact heads and successful workflow run IDs in:

```text
deploy/reconciliation/migration-state.yaml
deploy/reconciliation/final-cutover-evidence.yaml
```

A dirty worktree, non-fast-forward update, stale CI head, failed required job, or different tested/deployed SHA blocks cutover.

## 3. Initialize the operator-owned files

From the reconciliation `auto-assist` worktree:

```bash
make reconciliation-init
```

This creates ignored, mode-0600 working files when missing:

```text
deploy/reconciliation.env
deploy/reconciliation/migration-state.yaml
deploy/reconciliation/external-dependencies.yaml
deploy/reconciliation/final-cutover-evidence.yaml
deploy/reconciliation/runtime-projection.yaml
artifacts/reconciliation-hermes-home/config.yaml
```

Diff existing files against the current examples. Never overwrite real secrets blindly. Replace every `change-me`, `replace-*`, sample address, placeholder identity, and sample evidence path.

Generate unique, shadow-only values for at least:

```text
BASIC_AUTH_PASS
NEO4J_PASSWORD
AUTO_ROUTER_ADMIN_TOKEN
ASSISTX_RUNTIME_PROJECTION_HMAC_SECRET
AUTO_ROUTER_RUNTIME_PROJECTION_HMAC_SECRET
runbook, node-token, attestation, and KV HMAC secrets
```

The AssistX and router runtime-projection HMAC values must match each other but must not reuse a production secret.

## 4. Validate the Hermes authority boundary before Docker

The generated Hermes config must contain only the auto-router gateway:

```bash
make reconciliation-hermes-config-validate
```

Required result:

```text
HERMES_EXTERNAL_CONFIG: PASS
```

Manually confirm:

```yaml
model:
  default: auto/code
  base_url: http://auto-router-reconciliation:8088/v1

fleet:
  mode: external
```

There must be no `fleet.nodes` section. The standalone proxy is not part of this deployment.

## 5. Capture a current read-only production baseline

```bash
make reconciliation-preflight
```

Review and checksum the evidence for:

- all Compose projects, containers, images, networks, volumes, ports, mounts, and restart policies;
- systemd, cron, tmux, screen, nohup, sockets, and unmanaged processes;
- current AssistX, router, auto-assign, Hermes, OpenCode, Neo4j, Redis, LM Studio, llama.cpp, and related services;
- current repository branches and SHAs;
- Tailscale status, DNS, LAN addresses, and firewall/routing state;
- exact old-stack stop, start, enable, disable, and rollback commands;
- production state, backup, and restore locations.

Do not stop, restart, reconfigure, or write to the production stack during baseline capture.

## 6. Discover candidate LAN and Tailscale paths

Create the local LAN map using real current addresses, then run:

```bash
make reconciliation-discover-tailnet
```

Record and checksum:

```text
deploy/reconciliation/lan-runtime-map.json
artifacts/reconciliation-tailnet-candidates.json
artifacts/reconciliation-tailnet-candidates.json.sha256
```

Discovery is candidate-only. Reachability and `/v1/models` visibility do not prove physical ownership and do not admit a runtime.

## 7. Resolve physical runtime and loaded-model identity

For every runtime proposed for admission, collect evidence from the physical host. For LM Studio, prefer:

```bash
lms ps --json --host <physical-host>
```

Record separately:

```text
observer_node_id
runtime node/physical owner
runtime_instance_id
runtime kind and version
process identity
model_instance_id
exact server model ID
artifact fingerprint/content address
quantization
context length
capabilities
load owner
parallel slots
queue limit and timeout
LAN access URL
Tailscale access URL
observation and expiry timestamps
```

Rules:

- LAN and Tailscale URLs for one process share one `runtime_instance_id` and one admission counter.
- Unknown ownership is not admitted.
- Unknown capacity is zero.
- Unknown runtime version, model instance, artifact, quantization, or context blocks projection.
- Discovery never loads a model.

## 8. Populate and dry-run the canonical runtime projection

Edit:

```text
deploy/reconciliation/runtime-projection.yaml
```

It must contain one-step generation compare-and-swap data, named approval, expiring evidence, LAN-first ordering, Tailscale fallback, capacity, and complete model identity.

Validate without writing Neo4j:

```bash
make reconciliation-runtime-projection-plan
```

Required result:

```text
RUNTIME_PROJECTION_APPROVAL: DRY_RUN_PASS
```

Review and checksum both the manifest and dry-run evidence. Do not use `--apply` yet.

## 9. Render every shadow topology and executor profile

AssistX:

```bash
make reconciliation-render-direct
make reconciliation-render-router
make reconciliation-render-executor
make reconciliation-executor-containment-validate
```

Router:

```bash
cd ../auto-router
make reconciliation-init
make reconciliation-render
cd ../auto-assist
```

The rendered plans must prove:

- loopback-only host publication;
- isolated Compose projects, databases, volumes, and state;
- the dedicated `assistx_reconciliation_shared` network;
- no production resource reuse;
- no `auto-assign`;
- no public provider, broker, gateway, Paperclip inference lane, or hosted credential;
- no autoload, placement, unload, self-tasking, or recovery execution;
- the executor remains behind the `executor` profile and restart is disabled;
- non-root executor, read-only root, `cap_drop: ALL`, and `no-new-privileges`;
- no SSH, Docker socket, broad repository, NAS, or SSD mount;
- no web/browser/MCP tool capability;
- only the scoped Hermes home, evidence directory, and optionally one approved worktree.

Checksum every reviewed render.

## 10. Start the isolated shadow control plane

Start AssistX direct mode first:

```bash
make reconciliation-up-direct
```

Start the strict-offline router from its worktree:

```bash
cd ../auto-router
make reconciliation-up
cd ../auto-assist
```

Transition only the shadow AssistX API and worker to the router overlay:

```bash
make reconciliation-up-router
make reconciliation-status
```

Verify:

```bash
curl -fsS http://127.0.0.1:18000/health | jq
curl -fsS http://127.0.0.1:18088/health | jq
curl -fsS http://127.0.0.1:18088/v1/models | jq
```

Confirm the old production stack is still running and unchanged.

## 11. Atomically approve the shadow runtime generation

Only after the dry run and shadow Neo4j target are verified, apply the operator-reviewed manifest:

```bash
APPLY_RUNTIME_PROJECTION=YES make reconciliation-runtime-projection-approve
```

The transaction must:

- compare the expected current generation;
- refuse generation skips or replay conflicts;
- retire prior admissions in the same transaction;
- write runtime, model, artifact, path, capacity, approval, and expiry data;
- update `FleetProjectionState{name:'canonical'}` last;
- roll back all writes on any failure.

Then prove AssistX and auto-router converge on the exact generation, revision, and checksum:

```bash
make reconciliation-runtime-projection-verify
```

Required result:

```text
RUNTIME_PROJECTION_GATE: PASS
```

Record:

```text
artifacts/runtime-projection-approval-evidence.json
artifacts/runtime-projection-evidence.json
```

Also test in shadow that an active request on generation N can finish and release its original gate after generation N+1 is accepted, while new requests use N+1.

## 12. Prove container-level LAN preference and Tailscale fallback

From the router worktree:

```bash
export AUTO_ROUTER_ADMIN_TOKEN='<shadow-token>'
make reconciliation-network-verify
```

The evidence must prove from inside `auto-router-reconciliation`:

- the approved LAN path is reachable;
- the approved Tailscale path is reachable;
- both resolve to the same physical runtime/model identity;
- `/admin/admission` exposes one shared slot pool;
- LAN is selected when both paths work;
- Tailscale is selected when only the approved LAN test path is unavailable;
- selection returns to LAN after restoration and cache expiry;
- no model process is stopped or reloaded during failover testing.

Do not use host networking, alter production firewall rules, block the host Tailscale interface, or stop the physical runtime to manufacture the test.

## 13. Run strict-offline and authority tests

```bash
make reconciliation-verify
```

Additionally inspect the OpenAPI and context projection to prove:

- `/jobs/agent` and retired scheduler/discovery/mutation routes are absent;
- `/api/routes/request` blocks tool-capable agent-job creation;
- nonlocal lanes are rejected;
- AssistX context projection contains no Cerebras, OpenRouter, Groq, hosted provider, Paperclip inference provider, or public service;
- all public-provider environment variables are empty or absent;
- `auto-assign` is stopped/disabled in the proposed production plan;
- router cache deletion/rebuild does not change canonical AssistX authority.

Capture OpenAPI, context, environment-redaction, and process evidence with checksums.

## 14. Prove functional admission behavior

At the exact proposed SHAs and projection generation, record:

- direct completion against the physical runtime;
- routed completion through `127.0.0.1:18088` using an `auto/*` alias;
- sequential stability;
- one-slot overlap behavior;
- bounded queue or explicit 429/503 overflow;
- queue timeout behavior;
- cancellation and streaming-close permit release;
- router restart without model autoload or duplicate identity;
- AssistX restart and projection reconvergence;
- removal/rebuild of non-authoritative router SQLite/cache;
- trace events containing projection generation, runtime/model identity, transport, TPS, TTFT, tokens, latency, and errors.

A successful request alone is not sufficient evidence for capacity or identity.

## 15. Capture and verify the offline image rollback bundle

After the final images are built:

```bash
make reconciliation-images-capture
make reconciliation-images-verify-offline
```

Required evidence:

```text
artifacts/reconciliation-images/reconciliation-images.tar
artifacts/reconciliation-images/reconciliation-images.tar.sha256
artifacts/reconciliation-images/reconciliation-images.manifest.json
artifacts/reconciliation-images/reconciliation-images.restore-evidence.json
```

The verification must execute `docker load` from the local bundle, require no pull or internet access, and prove every recorded image ID is available.

## 16. Run one contained Hermes synthetic lifecycle

Only after sections 1–15 pass and the operator approves enabling the shadow executor:

```bash
make reconciliation-executor-up
```

Inside the executor, verify:

```text
fleet.mode = external
fleet.nodes absent
hermes fleet status reads auto-router admission
hermes fleet serve rejected
hermes fleet discover rejected
model intent = auto/code
```

Run one non-sensitive synthetic task and prove:

- AssistX allocation and reservation;
- fenced claim and stale-claim rejection;
- heartbeat and checkpoint;
- semantic `auto/*` request with task/run/claim/capability/workflow/session metadata;
- routed local inference;
- evidence and completion;
- no self-created follow-up;
- no second fleet router or slot authority;
- no repository mutation unless one dedicated test worktree was explicitly mounted;
- no model load, unload, restart, public call, or recovery action.

Stop the Hermes adapter after the test unless continued shadow operation is separately approved.

## 17. Backup and rollback rehearsal

Before recommending cutover:

- identify the exact production Neo4j edition, version, database, and deployment form;
- create a real production backup using its supported mechanism;
- checksum the backup;
- record the exact restore command;
- perform an isolated restore rehearsal where practical;
- back up required Redis/SQLite/config/state artifacts;
- prove old-stack restart commands;
- rehearse stopping and recreating **only** the shadow stack;
- prove the old production stack continues throughout the rehearsal;
- record rollback time, commands, dependencies, and manual steps.

Never restore over production during preparation.

## 18. Complete the two evidence contracts

Populate:

```text
deploy/reconciliation/migration-state.yaml
deploy/reconciliation/final-cutover-evidence.yaml
```

The final evidence contract must include exact green CI SHAs/run IDs and checksummed proof for:

- Hermes external mode and disabled standalone commands;
- runtime projection approval and convergence;
- old-generation lease preservation and evidence expiry;
- executor containment;
- offline image restoration;
- strict-offline authority boundaries;
- control-room visibility and shared snapshot cache;
- no blockers.

Then run:

```bash
make reconciliation-state-validate
make reconciliation-dependencies-validate
make reconciliation-hermes-config-validate
make reconciliation-executor-containment-validate
make reconciliation-images-verify-offline
make reconciliation-runtime-projection-verify
make reconciliation-final-evidence-validate
make reconciliation-cutover-gate
make reconciliation-report
```

Every command must pass at the exact deployment SHAs. A passing gate is evidence for review; it is not permission to mutate production.

## 19. Mandatory approval stop

Present the operator with:

- repository SHAs and green CI run IDs;
- current production baseline and checksums;
- rendered shadow and proposed production Compose plans;
- runtime/model identity and signed projection evidence;
- LAN/Tailscale failover evidence;
- admission/cancellation/restart evidence;
- Hermes synthetic lifecycle evidence;
- containment and image-restore proof;
- Neo4j backup and restore proof;
- exact production cutover commands;
- exact rollback commands and trigger thresholds;
- generated reconciliation report;
- remaining risks and required maintenance window.

Do not continue without explicit production-cutover approval.

## 20. Controlled production cutover after approval only

Use the exact, previously reviewed machine-specific command record. The intended order is:

1. pause external intake;
2. drain or checkpoint active work;
3. confirm no active claims will be orphaned;
4. verify backup and rollback artifacts again;
5. stop old executors;
6. stop old worker;
7. stop and disable old `auto-assign` restart;
8. stop old router;
9. stop old AssistX API;
10. start new AssistX/Neo4j/Redis authority with Hermes disabled;
11. verify health, canonical generation, and state;
12. start new strict-offline auto-router;
13. verify projection convergence, models, admission, and path selection;
14. run one production-safe routed canary;
15. switch one client to auto-router;
16. observe errors, queueing, TPS, TTFT, and traces;
17. enable Hermes external-mode executor at concurrency one;
18. run one fenced production-safe task;
19. gradually reopen intake;
20. keep old rollback artifacts and commands immediately available.

Do not enable autonomous recovery, model mutation, additional runtimes, multiple Hermes sessions, OpenCode execution, or broad repository mounts during the initial cutover window.

## 21. Immediate rollback triggers

Rollback immediately on any of these conditions:

- AssistX/Neo4j unavailable or state divergence;
- projection generation/checksum mismatch or repeated signature/expiry failures;
- unknown or duplicated runtime/model identity;
- admission leak, stuck permit, unbounded queue, or persistent cancellation failure;
- public-provider attempt or hosted credential detection;
- `auto-assign`, Paperclip inference, or Hermes standalone fleet authority appears;
- LAN/Tailscale paths resolve to different physical processes;
- unexpected model load/unload/restart;
- Hermes self-tasking, stale-claim acceptance, or unfenced execution;
- missing trace/task visibility in the control room;
- sustained error, latency, throughput, memory, disk, or queue threshold breach;
- inability to restore the old stack with the recorded local artifacts.

Rollback order:

1. pause new intake;
2. disable the new Hermes executor;
3. drain/checkpoint new work where safe;
4. revert the switched client endpoint;
5. stop only the new router and AssistX services;
6. restart the old AssistX, router, worker, and executors using recorded commands;
7. verify old health and one canary;
8. preserve all new-stack logs, projection documents, databases, and evidence;
9. mark the migration `ROLLED_BACK` and document the trigger.

Do not delete the failed new stack or its evidence until diagnosis is complete.

## Required final report fields

```text
STATUS: PASS | BLOCKED | ROLLED_BACK
PRODUCTION_CHANGED: yes | no
PUBLIC_INFERENCE_FOUND: yes | no
SHADOW_STACK_HEALTHY: yes | no
CI_GATE: pass | blocked
HERMES_AUTHORITY_GATE: pass | blocked
RUNTIME_IDENTITY_GATE: pass | blocked
RUNTIME_PROJECTION_GATE: pass | blocked
CAPACITY_GATE: pass | blocked
NETWORK_PATH_GATE: pass | blocked
LAN_TAILSCALE_FAILOVER_GATE: pass | blocked
STATE_AUTHORITY_GATE: pass | blocked
EXECUTOR_CONTAINMENT_GATE: pass | blocked
AIRGAP_IMAGE_RESTORE_GATE: pass | blocked
CONTROL_ROOM_GATE: pass | blocked
HERMES_SYNTHETIC_GATE: pass | blocked
NEO4J_BACKUP_GATE: pass | blocked
ROLLBACK_REHEARSAL: pass | blocked
CUTOVER_RECOMMENDED: yes | no
```

The system is ready for operator review only when all required gates pass, all artifacts are checksummed, all deployed SHAs match green CI, the blocker list is empty, and production remains unchanged.
