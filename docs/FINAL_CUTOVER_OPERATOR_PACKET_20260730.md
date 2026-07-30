# Final cutover operator packet — 2026-07-30

This packet is the final operator-facing sequence for moving from the old local fleet stack to the reconciled AssistX/auto-router/Hermes stack.

It does not authorize production changes. The machine-side agent must stop before the production mutation section and present the completed ledger, report, exact commands, backup evidence, and rollback thresholds for approval.

## 1. Required repository heads

All reconciliation worktrees must be clean and pinned to branch:

```text
full-auto-reconciliation-20260730
```

Record the current full 40-character SHA for every repository in:

```text
deploy/reconciliation/migration-state.yaml
```

At minimum, pin:

```text
auto-assist
auto-router
hermes-agent
fleet-llm-profiles
lms
```

Also record the companion repositories used or retired by the deployment.

Do not rely on a SHA printed in this document. Fetch the branch and record the actual current head on the machine.

## 2. Update the reconciliation worktrees

From each clean reconciliation worktree:

```bash
git fetch origin
git switch full-auto-reconciliation-20260730
git pull --ff-only origin full-auto-reconciliation-20260730
git status --short
git rev-parse HEAD
```

A dirty worktree, non-fast-forward update, missing branch, or unexpected commit blocks cutover.

## 3. Recreate local configuration from the current examples

Do not overwrite working secret files blindly. Diff the current local files against the examples and merge the new fields.

AssistX:

```text
deploy/reconciliation.env.example
deploy/reconciliation/migration-state.example.yaml
deploy/reconciliation/lan-runtime-map.example.json
```

Router:

```text
.env.example
config/providers.reconciliation.yaml
compose.reconciliation.yml
```

The router reconciliation environment must include unique, non-production values and real runtime details:

```env
AUTO_ROUTER_ADMIN_TOKEN=<unique-shadow-token>
RECONCILIATION_ASSISTX_NETWORK=assistx_reconciliation_shared
RECONCILIATION_RUNTIME_NODE_ID=<physical-node-id>
RECONCILIATION_RUNTIME_INSTANCE_ID=<stable-runtime-instance-id>
RECONCILIATION_PARALLEL_SLOTS=<verified-integer>
RECONCILIATION_QUEUE_LIMIT=<bounded-integer>
RECONCILIATION_QUEUE_TIMEOUT_SECONDS=<bounded-seconds>
RECONCILIATION_LAN_BASE_URL=http://<rfc1918-address>:1234/v1
RECONCILIATION_LMSTUDIO_BASE_URL=http://<existing-approved-path>:1234/v1
RECONCILIATION_TAILSCALE_BASE_URL=http://<100.64.0.0/10-address>:1234/v1
RECONCILIATION_MODEL_ID=<exact-loaded-model-id>
RECONCILIATION_CONTEXT_WINDOW=<verified-context>
```

Do not use the example IP addresses as real configuration.

## 4. Capture current production state again

The baseline must be current enough for the maintenance window:

```bash
cd /home/scott/git/reconciliation-20260730/auto-assist
make reconciliation-preflight
```

Review and checksum:

- Compose projects and containers;
- images and restart policies;
- ports and networks;
- volumes and bind mounts;
- systemd, cron, tmux, screen, and unmanaged processes;
- production Neo4j and Redis locations;
- old router, AssistX, auto-assign, and executor health;
- Tailscale state;
- exact old-stack stop and restart commands.

The old stack must remain unchanged during this step.

## 5. Discover LAN and Tailscale candidates

Create the local LAN map from real machine addresses:

```bash
cp deploy/reconciliation/lan-runtime-map.example.json \
  deploy/reconciliation/lan-runtime-map.json
chmod 600 deploy/reconciliation/lan-runtime-map.json
$EDITOR deploy/reconciliation/lan-runtime-map.json
sha256sum deploy/reconciliation/lan-runtime-map.json \
  > artifacts/reconciliation-lan-runtime-map.sha256
```

Then collect candidate private paths:

```bash
make reconciliation-discover-tailnet
```

Record these files and checksums in the migration ledger:

```text
artifacts/reconciliation-tailnet-candidates.json
artifacts/reconciliation-tailnet-candidates.json.sha256
deploy/reconciliation/lan-runtime-map.json
artifacts/reconciliation-lan-runtime-map.sha256
```

Discovery is candidate-only. A peer is not admitted merely because it is online or has port 1234 reachable.

## 6. Resolve physical runtime and model ownership

For every candidate that may be admitted, record evidence from the physical host. For LM Studio, use the official CLI where available:

```bash
lms ps --json --host <physical-host>
```

Record separately:

```text
observer_node_id
runtime_node_id
runtime_instance_id
runtime_kind and version
model_instance_id
model key and artifact fingerprint
quantization
context length
load owner
parallel slots
LAN access path
Tailscale access path
observation and expiry timestamps
```

The LAN and Tailscale paths must point to the same physical runtime and share one slot pool.

## 7. Render and inspect the shadow deployment

AssistX:

```bash
cd /home/scott/git/reconciliation-20260730/auto-assist
make reconciliation-render-direct
make reconciliation-render-router
```

Router:

```bash
cd /home/scott/git/reconciliation-20260730/auto-router
make reconciliation-init
make reconciliation-render
```

Inspect the rendered files for:

- host publications bound only to `127.0.0.1`;
- the shared `assistx_reconciliation_shared` Docker network;
- no production volume, network, container, or database reuse;
- no hosted provider or public endpoint;
- no `auto-assign` service;
- no autoload, unload, dispatcher, gateway, or self-task behavior;
- explicit runtime identity, slots, queue limit, and ordered access paths;
- a required router admin token.

Checksum every reviewed render and record it in the ledger.

## 8. Start or rebuild the isolated shadow stack

Start AssistX direct mode first:

```bash
cd /home/scott/git/reconciliation-20260730/auto-assist
make reconciliation-up-direct
```

Start the router:

```bash
cd /home/scott/git/reconciliation-20260730/auto-router
make reconciliation-up
```

Transition AssistX to the router overlay:

```bash
cd /home/scott/git/reconciliation-20260730/auto-assist
make reconciliation-up-router
```

Verify:

```bash
curl -fsS http://127.0.0.1:18000/health | jq
curl -fsS http://127.0.0.1:18088/health | jq
curl -fsS http://127.0.0.1:18088/v1/models | jq
```

Confirm the old production services were not recreated, stopped, renamed, or attached to the shadow networks.

## 9. Prove container LAN and Tailscale reachability

From the router worktree, export the configured shadow admin token and run:

```bash
export AUTO_ROUTER_ADMIN_TOKEN='<unique-shadow-token>'
make reconciliation-network-verify
```

This writes:

```text
artifacts-reconciliation/network-path-evidence.json
artifacts-reconciliation/network-path-evidence.json.sha256
```

The check must prove from inside `auto-router-reconciliation` that both configured URLs are reachable and that `/admin/admission` reports the expected runtime and approved path list.

If the Tailscale IP is not reachable from the container, stop. Inspect host forwarding/firewall policy. Do not switch the stack to host networking merely to bypass the gate.

## 10. Prove LAN preference and Tailscale fallback

Perform this only against the shadow router.

### LAN preference

With both real paths configured and reachable:

1. restart only the shadow router;
2. send one routed completion;
3. query `/admin/admission`;
4. verify `selected_transport` is `lan` and the selected URL is the expected RFC1918 path.

### Tailscale fallback

Without changing the physical model process:

1. set the shadow `RECONCILIATION_LAN_BASE_URL` to a deliberately unreachable RFC1918 address reserved for this test;
2. keep the real Tailscale path configured;
3. render and review the shadow router configuration;
4. recreate only `auto-router-reconciliation`;
5. send one routed completion;
6. verify `/admin/admission` selects `tailscale`;
7. verify the same `runtime_instance_id` and admission counters remain in use;
8. restore the real LAN URL;
9. recreate only the shadow router;
10. verify selection returns to `lan` after the path cache refresh.

Do not block the host's Tailscale interface, modify production firewall rules, or stop the physical runtime to simulate this test.

Record all commands, timestamps, selected paths, runtime IDs, and admission snapshots.

## 11. Re-run functional gates

At the current branch heads, repeat and record:

- direct completion;
- routed completion;
- sequential stability;
- one-slot overlap behavior;
- bounded queue or explicit rejection;
- cancellation safety;
- router restart and state rebuild;
- AssistX restart and state rebuild;
- deletion/rebuild of non-authoritative router cache;
- confirmation that AssistX/Neo4j remains canonical;
- confirmation that no model load occurred.

Run the strict offline verifier:

```bash
cd /home/scott/git/reconciliation-20260730/auto-assist
make reconciliation-verify
```

## 12. Run the Hermes synthetic lifecycle gate

Only after every previous gate passes and the operator approves the shadow executor:

```bash
cd /home/scott/git/reconciliation-20260730/auto-assist
make reconciliation-executor-up
```

Run one synthetic, non-sensitive task and prove:

- allocation and reservation;
- fenced claim;
- heartbeat;
- routed local inference;
- checkpoint/evidence;
- completion;
- stale-claim rejection;
- no self-created follow-up task;
- no repository mutation;
- no model load/unload/restart.

Stop the shadow Hermes adapter after the gate unless continued shadow operation is separately approved.

## 13. Create and verify the production Neo4j backup

Use the backup mechanism supported by the actual production Neo4j edition and deployment. Record:

- version and database name;
- backup method;
- path;
- SHA-256 checksum;
- creation timestamp;
- exact restore command;
- isolated restore-test result when practical.

Do not overwrite or restore the production database during preparation.

## 14. Populate the final ledger

Update:

```text
deploy/reconciliation/migration-state.yaml
```

Every required gate must be backed by an artifact or command output. In particular, record:

```text
checks.tailnet_discovery: pass
checks.container_network_paths: pass
checks.lan_tailscale_failover: pass
```

Each admitted runtime must contain both `lan` and `tailscale` access-path records, per-path reachability evidence, the selected path, and:

```text
lan_preference_probe: pass
tailscale_fallback_probe: pass
shared_admission_probe: pass
```

Unknown or unverified values remain blockers.

## 15. Validate and render the operator report

```bash
cd /home/scott/git/reconciliation-20260730/auto-assist
make reconciliation-state-validate
make reconciliation-cutover-gate
make reconciliation-report
```

All three commands must succeed at the exact repository SHAs proposed for production.

The generated report must say:

```text
PRODUCTION_CHANGED: no
PUBLIC_INFERENCE_FOUND: no
SHADOW_STACK_HEALTHY: yes
RUNTIME_IDENTITY_GATE: pass
CAPACITY_GATE: pass
TAILNET_DISCOVERY_GATE: pass
NETWORK_PATH_GATE: pass
LAN_TAILSCALE_FAILOVER_GATE: pass
STATE_AUTHORITY_GATE: pass
HERMES_SYNTHETIC_GATE: pass
ROLLBACK_REHEARSAL: pass
CUTOVER_RECOMMENDED: yes
```

A passing report is still not authorization.

## 16. Exact command evidence

Create an untracked, mode-0600 command record containing the real machine-specific commands for:

- pause external intake;
- drain/checkpoint active work;
- stop old executors;
- stop old worker;
- stop and disable old `auto-assign` restart;
- stop old router;
- stop old AssistX API;
- create/verify the production backup;
- render the final production Compose plan;
- start the new AssistX API and worker with Hermes disabled;
- start the new router;
- run production health and synthetic gates;
- switch one client;
- enable Hermes at concurrency one;
- gradually reopen intake;
- reverse every step for rollback.

Reference the command record from:

```text
cutover.exact_commands_evidence_path
rollback.exact_commands_evidence_path
```

Do not use generic commands copied from this document in place of the machine's real project names, files, and services.

## 17. Mandatory stop before production mutation

The machine-side agent must now return the report, checksums, exact command record, backup evidence, rollback thresholds, and unresolved risks to the operator.

The following require explicit approval with operator name and timestamp in the ledger:

```text
approvals.hermes_shadow_executor
approvals.production_backup
approvals.production_cutover
```

Do not execute the production mutation sequence before the final approval.

## 18. Approved production sequence

After approval, execute only the recorded commands in this order:

1. announce maintenance;
2. pause intake;
3. drain or checkpoint active tasks;
4. capture queue, claim, lease, and process state;
5. stop old executors;
6. stop old worker;
7. stop/disable old `auto-assign`;
8. stop old router;
9. stop old AssistX API;
10. verify the production backup and checksum;
11. render and checksum the final production configuration;
12. start new state dependencies as required;
13. start new AssistX API and worker with Hermes disabled;
14. start strict-offline router;
15. verify API, graph, runtime identity, capacity, LAN/Tailscale paths, and offline policy;
16. run a production synthetic completion;
17. run a production synthetic AssistX task;
18. switch one internal client;
19. observe the approved canary interval;
20. enable Hermes at concurrency one;
21. reopen intake gradually;
22. retain all old rollback assets until the rollback window closes.

## 19. Immediate rollback triggers

Rollback immediately when any of these occurs:

- public inference appears or receives traffic;
- physical runtime ownership is ambiguous;
- LAN and Tailscale paths produce duplicate runtime/capacity records;
- a one-slot runtime receives concurrent generations;
- the Tailscale fallback selects a different physical process than the LAN path;
- claims or leases are duplicated or lost;
- new state cannot rebuild after restart;
- errors, latency, or queue age cross the approved threshold;
- any load, unload, restart, recovery, or repository action targets the wrong object;
- Neo4j consistency is uncertain.

Execute only the recorded rollback commands. Preserve the failed new-stack state for diagnosis. Database restoration remains a separate operator decision.

## 20. Completion standard

The cutover is complete only when:

- all final production health and task gates pass;
- the selected runtime path and capacity are correct;
- one mobile/off-site Tailscale fallback case has been proven in shadow or controlled production canary;
- old-stack restart remains possible throughout the rollback window;
- the final report and checksums are archived;
- no hosted inference provider was configured or called;
- no old volume, database, image, or configuration was destructively removed.
