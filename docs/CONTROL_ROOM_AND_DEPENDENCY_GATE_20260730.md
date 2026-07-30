# Fleet control room and external dependency gate — 2026-07-30

## Purpose

The reconciled control plane is not production-ready merely because requests complete. The operator must be able to identify what work is running, which physical runtime and loaded model process owns it, which private path was selected, how capacity is being consumed, how the model is performing, and which external dependency can stop or corrupt the fleet.

This change makes `/control-room` the canonical operator interface and makes an external dependency registry a prerequisite for `make reconciliation-cutover-gate`.

## Canonical UI

The following legacy dashboard routes redirect to `/control-room`:

- `/`
- `/live`
- `/operations`
- `/fleet-dashboard`
- `/command-center`
- `/strategy`
- `/routing`
- `/fleet`

Their API endpoints remain available for compatibility. The redirect removes overlapping operator pages without deleting data or automation surfaces during migration.

The control room uses a two-second server-sent event stream with a normal JSON endpoint as fallback:

```text
GET /api/control-room/overview
GET /api/control-room/stream
```

Both require the existing AssistX UI authentication.

## What the control room shows

### Physical runtime matrix

Each row represents one physical runtime process identity, not one URL. The row includes:

- physical node;
- runtime instance ID;
- runtime kind and version;
- `HEADLESS`, `LM_STUDIO`, or `UNKNOWN` mode;
- loaded model identities;
- selected LAN or Tailscale path;
- active and total slots;
- bounded queue usage;
- current status.

Expanding the row exposes the complete non-secret runtime record, including approved paths and admission counters.

`HEADLESS` is reported only when an explicit observation says so or when the runtime kind is a recognized non-GUI server such as `llama.cpp`, `llama-server`, `vLLM`, or Ollama. Missing information is rendered as `UNKNOWN`.

### Execution feed

The primary label is the human task title. UUIDs are retained only in expandable technical details. Each event can display:

- task title, kind, repository, and stage;
- executor and model;
- physical runtime and runtime kind;
- selected private transport;
- queue wait;
- time to first token;
- tokens per second;
- prompt and completion tokens;
- duration, status, and error class;
- task, run, and runtime IDs.

Older `AgentRun` rows that do not contain these fields remain visible with explicit unresolved values. The UI must never infer a physical owner from a model name or localhost URL.

### Model performance

The performance table aggregates the existing `ModelPerf` ledger by node and model:

- run count;
- average tokens per second;
- average time to first token;
- average latency;
- error percentage;
- average quality score.

The collector is additive. It does not start benchmarks, load models, or make a stale model routable.

### Dependency state

The live panel probes or reports:

- Neo4j;
- Redis;
- strict-offline auto-router;
- authenticated router admission telemetry;
- Tailscale CLI or current candidate evidence;
- required and optional mounted storage;
- Hermes and OpenCode availability;
- public inference credential absence;
- auto-assign retirement;
- Paperclip disposition;
- tool web-egress mode.

A missing required dependency degrades the screen. It is not silently marked healthy.

## Dependency cutover registry

Initialize the operator-owned registry with:

```bash
make reconciliation-init
```

This creates an ignored mode-0600 file when absent:

```text
deploy/reconciliation/external-dependencies.yaml
```

It is copied from:

```text
deploy/reconciliation/external-dependencies.example.yaml
```

The registry includes runtime, platform, private-network, storage, restore, security, authority, executor, and optional integration dependencies. Every required dependency must have:

- the expected state;
- current status;
- owner;
- failure impact;
- exact probe;
- non-placeholder evidence;
- rollback action.

Validate it independently:

```bash
make reconciliation-dependencies-validate
```

The production gate now runs this validation first:

```bash
make reconciliation-cutover-gate
```

The gate rejects:

- unknown or degraded required dependencies;
- a required healthy dependency that is not healthy;
- a required disabled dependency that is not disabled;
- missing or placeholder evidence;
- missing required dependency IDs;
- public inference policy drift;
- non-private inference egress;
- an executor default other than deny;
- lack of an offline image-restore requirement;
- a registry not explicitly marked `ready`.

## Contained production overlay

The repository now includes:

```text
compose.production.reconciled.yml
```

It must be applied only after the operator supplies verified image references, persistent volume identities, secrets, and the private shared AssistX/router network.

The overlay intentionally:

- uses locally cached images and `pull_policy: never`;
- requires explicit production volume names;
- removes `host.docker.internal` mappings;
- removes the legacy `git_default` network;
- clears Paperclip, auto-assign, and public-provider configuration;
- binds host APIs to loopback;
- disables recovery execution and self-task generation;
- removes source-code, SSH, SSD, NAS, MCP, OpenCode-host-binary, and whole-git-root mounts;
- drops all Linux capabilities from Hermes;
- enables `no-new-privileges`;
- makes the Hermes filesystem read-only except for approved volumes;
- gives Hermes only local code-execution toolsets by default;
- keeps Hermes behind the explicit `executor` profile.

The base overlay is deliberately insufficient for arbitrary repository mutation. One approved repository task can use a locally copied and reviewed version of:

```text
compose.executor-repository.example.yml
```

That overlay mounts exactly one worktree at `/worktree`. SSH and remote recovery access require a different, separately approved overlay.

## Production rendering

A final render should use the exact production files selected by the operator, for example:

```bash
docker compose \
  --env-file deploy/production.env \
  -f docker-compose.yml \
  -f compose.prod.yml \
  -f compose.production.reconciled.yml \
  config > artifacts/reconciliation-render/assistx-production.yaml
```

Review and checksum the result. The render must not contain:

- public provider credentials or endpoints;
- `auto-assign`;
- Paperclip unless separately approved as an optional integration;
- host SSH keys;
- Docker socket;
- whole repository roots;
- NAS or SSD roots;
- `git_default`;
- `host.docker.internal`;
- unrestricted web/search/browser toolsets;
- placeholder passwords;
- unnamed or unverified persistent volumes.

## Telemetry producers still required on the live fleet

The UI can only display facts that producers emit. Before calling observability complete, runtime adapters and route-event writers should populate these fields on new events:

```text
request_id
task_id
task_title
claim_id
executor
stage
runtime_node_id
runtime_instance_id
runtime_kind
model_instance_id
model_key
quantization
context_length
selected_access_url
selected_transport
queue_wait_ms
time_to_first_token_ms
generation_ms
prompt_tokens
completion_tokens
tokens_per_second
status
error_class
started_at_ts
ended_at_ts
```

Node observations should also include, where supported:

```text
process_id
service_manager
runtime_version
headless
load_owner
loaded_at_ts
cpu_percent
ram_used_bytes
gpu_utilization_percent
vram_used_bytes
gpu_temperature_c
runtime_uptime_seconds
active_requests
queued_requests
```

Unsupported metrics must remain `null` or `unknown`; adapters must not fabricate zeros.

## Acceptance criteria

The observability and dependency milestone passes only when:

1. `/control-room` is reachable and receives fresh SSE snapshots.
2. Every active request has a human task title or an explicit unidentified label.
3. Every admitted request identifies one physical runtime and one shared slot pool.
4. Headless versus LM Studio mode is visible or explicitly unknown.
5. LAN/Tailscale selection is visible.
6. Queue, slot, tokens-per-second, latency, and error metrics update from real evidence.
7. Required dependency failures appear in the top summary.
8. The external dependency registry passes with no placeholders.
9. The final production render contains only approved mounts, networks, images, and integrations.
10. `make reconciliation-cutover-gate` passes at the exact proposed production SHAs.

A visually healthy control room is not itself authority to cut over. It is an operator visibility and evidence surface layered on top of the existing approval gate.
