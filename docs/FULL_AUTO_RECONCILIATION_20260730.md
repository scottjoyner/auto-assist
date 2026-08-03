# Full auto reconciliation — 2026-07-30

Status: **authoritative migration decision for branch `full-auto-reconciliation-20260730`**

## Executive decision

The fleet has accumulated overlapping discovery, scheduling, routing, health, profile, and benchmarking implementations. The failure mode is not a lack of features; it is multiple components independently deciding what exists, what is loaded, which node is healthy, and what should run next.

The reconciled system has one control plane and one narrow inference gateway:

```text
Hermes / OpenCode / operator / workloads
                  |
                  v
       auto-router (offline gateway only)
       - OpenAI-compatible request facade
       - admission control and backpressure
       - endpoint adapter normalization
       - no task ownership
       - no autonomous model loading
                  |
                  v
       auto-assist / AssistX + Neo4j
       - canonical node and endpoint identity
       - loaded-model observations
       - benchmark and capability evidence
       - health, priority, reservations, claims, leases
       - recovery policy and operator controls
                  |
                  v
       LM Studio Link / LM Studio server / llama-server
       - runtime execution only
       - node-local process ownership
```

**Do not create another inference-server repository.** Reuse `auto-router` as the protocol gateway and put all durable fleet state, ranking, assignment, and reconciliation in AssistX.

## Non-negotiable invariants

1. **Offline inference only.** Groq, Cerebras, OpenRouter, hosted agent gateways, Grok, and every other public inference provider are excluded from discovery and routing. Their presence in configuration is a startup failure in strict mode, not a low-priority fallback.
2. **One assignment authority.** AssistX owns allocation, reservations, claims, leases, heartbeats, stale recovery, and migration. `auto-assign` must not run in the reconciled deployment.
3. **One inventory identity model.** A model record is keyed by the physical serving runtime, not by whichever client URL exposed it.
4. **No automatic load/unload during discovery.** Discovery may observe. Loading or unloading requires an explicit AssistX reservation/recovery action and a node-local runtime adapter.
5. **Observed state expires.** A model is routable only when its loaded observation, health probe, benchmark confidence, and capacity observation are fresh.
6. **Backpressure before retry.** A one-slot node must advertise capacity one. The gateway queues or rejects excess work; it must never hammer the endpoint until it crashes.
7. **Static profiles are desired-state inputs, not live truth.** YAML, markdown, SQLite, and benchmark artifacts cannot override a recent runtime observation.
8. **Hermes is the primary execution provider.** OpenCode is an explicitly lower-priority execution integration. Neither is an inference-state authority.

## Repository disposition

| Repository | Decision | Reconciled responsibility |
|---|---|---|
| `auto-assist` | **Keep and make authoritative** | Control plane, Neo4j schema, discovery ingestion, loaded-state reconciliation, scheduling, reservation/claim/lease, health and recovery, operator UI |
| `auto-router` | **Keep but cut down** | Offline OpenAI-compatible gateway, normalized runtime adapters, admission control, request forwarding, non-durable short queue |
| `auto-assign` | **Retire** | No production service. Preserve as migration history until useful tests/contracts are absorbed into AssistX |
| `hermes-agent` | **Keep** | Primary agent/executor consuming AssistX claims and the offline gateway; no independent fleet discovery or model loading |
| `fleet-llm-profiles` | **Keep as canonical desired-state repo** | Per-node desired runtime, hardware constraints, model placement intent, launcher/unit templates, benchmark references |
| `fleet-inference-configs` | **Merge then retire** | Move unique launchers and proven flags into `fleet-llm-profiles`; stop deploying cron-managed servers from this repo |
| `fleet-resilience` | **Absorb then retire as a daemon** | Reuse read-only probes and tests inside AssistX/node adapters; do not run a separate actor/standby brain or remediation authority |
| `lms` | **Keep as benchmark/probe library** | LM Studio CLI bridge, loaded-process observation, deterministic benchmark evidence; rename its CLI so it never shadows official `lms` |
| `ai-research-vault` | **Keep as workload** | Use the shared offline gateway/registry; never discover providers, load models, or choose fleet nodes independently |

## Why the present deployment fails

### Split scheduling authority

AssistX already implements allocation reservations, claims, leases, heartbeats, migration, reconciliation, and recovery. `auto-assign` implements the same lifecycle in another process and local store. Even when both write to Neo4j, two reconcilers can evaluate different snapshots and create contradictory decisions.

### Discovery creates load and instability

The current headless migration lets broad auto-discovery route concurrent work to single-slot endpoints. At least one documented node crashes under that load and is then revived by cron. The recovery mechanism therefore masks a routing/admission-control defect.

### LM Studio Link destroys endpoint identity

LM Studio Link can expose a remote loaded model through a local client/server view. Treating the returned base URL as the physical server makes multiple remote runtimes appear to be `localhost`, causing:

- duplicate model records;
- false belief that a model is local to the observer;
- attempts to load a model that is already loaded remotely;
- incorrect capacity, latency, and affinity scoring;
- unload/restart actions aimed at the wrong host.

### Configuration is being treated as runtime truth

The fleet manifests contain useful desired state but also stale roles, endpoints, service lists, and hardware corrections. Static files cannot be the final answer to “what is serving now?”

## Canonical endpoint and loaded-model contract

Every runtime observation must preserve both the observer/client path and the physical runtime owner.

```json
{
  "observation_id": "uuid",
  "observed_at": "RFC3339",
  "observer_node_id": "x1-370",
  "access_url": "http://127.0.0.1:1234/v1",
  "transport": "lmstudio_link",
  "runtime_node_id": "xwing",
  "runtime_instance_id": "lmstudio:xwing:1234",
  "runtime_kind": "lmstudio",
  "runtime_version": "unknown-or-reported",
  "model_instance_id": "runtime-generated-id",
  "model_key": "publisher/model-or-gguf-fingerprint",
  "model_path_fingerprint": "optional-hash",
  "quantization": "Q4_K_M",
  "context_length": 32768,
  "loaded": true,
  "load_owner": "operator|assistx|external",
  "capacity": {
    "parallel_slots": 1,
    "active_requests": 0,
    "queued_requests": 0
  },
  "health": {
    "models_probe_ok": true,
    "completion_probe_ok": true,
    "latency_ms": 42
  },
  "source": {
    "kind": "official_lms_ps",
    "host_argument": "xwing"
  }
}
```

Identity rules:

- `access_url` is not identity.
- `observer_node_id` is not necessarily `runtime_node_id`.
- `runtime_instance_id + model_instance_id` identifies a loaded process.
- `model_key + quantization + runtime fingerprint` identifies compatibility.
- `/v1/models` proves API visibility only; it does not prove physical ownership or load authority.
- For LM Studio, `lms ps --json --host <physical-host>` is the preferred loaded-process source when available.
- A completion canary is required before an endpoint becomes routable.

## Strict offline provider policy

`auto-router` must start in strict local mode by default for this deployment.

Allowed endpoint classes:

- loopback addresses;
- RFC1918 LAN addresses;
- Tailscale addresses and approved MagicDNS names;
- Unix sockets or node-local adapters;
- explicitly allowlisted OpenCode local endpoints.

Denied endpoint classes:

- all public HTTP(S) inference endpoints;
- any provider requiring an external API key;
- brokered gateways capable of escaping to public providers;
- provider records whose physical runtime owner cannot be resolved.

The deny decision must occur at configuration load and again before each request. Environment variables must not be able to silently re-enable a public provider while strict mode is active.

## Runtime loading policy

The default posture is **observe and route what is already loaded**.

A load action is permitted only when all are true:

1. an AssistX reservation explicitly requests a model load;
2. the physical runtime owner is resolved;
3. the node reports sufficient memory/headroom;
4. no compatible loaded instance already exists anywhere acceptable;
5. the node adapter supports idempotent load inspection;
6. the action has a lease and idempotency key;
7. the loaded process passes a completion canary;
8. rollback/unload targets the same runtime instance.

Discovery, benchmarking, research-vault jobs, Hermes, and OpenCode may never call load/unload directly.

## Immediate containment sequence

Perform this before further optimization:

1. Stop and disable `auto-assign`.
2. Run AssistX without `compose.overlay.yml` or set the overlay to router-only.
3. Replace the router provider configuration with strict offline-only providers; remove all public API keys from the service environment.
4. Disable background routing to every endpoint with unknown physical ownership.
5. Set endpoint capacity from the runtime (`parallel_slots`); default unknown capacity to zero, not one.
6. Stop the broad `:1234` hammer loop and cron-based self-heal on unstable nodes.
7. Restore LM Studio on nodes where it was demonstrably more stable than the headless replacement. Do not treat returning to LM Studio as architectural failure; stability is the acceptance gate.
8. Use the official LM Studio CLI bridge to inventory loaded processes per physical host.
9. Run one completion canary and a short deterministic benchmark before admitting each model instance.
10. Freeze automatic model loading until the identity contract and adapter tests pass end to end.

## Migration phases

### Phase 0 — freeze and observe

- Strict offline mode.
- Router-only overlay.
- No auto-assign.
- No autonomous load/unload/restart.
- No profile deployment changes.
- Capture physical runtime/process observations and compare them with Neo4j.

Exit: the Operations UI shows each loaded model exactly once with correct physical host and slot count.

### Phase 1 — consolidate state

- Add the endpoint/model observation contract to AssistX.
- Import desired state from `fleet-llm-profiles` as versioned declarations.
- Import benchmark evidence from `lms` as evidence records with age/confidence.
- Convert `fleet-resilience` probes into AssistX collectors or node-adapter checks.
- Remove auto-router and auto-assign SQLite data from decision paths.

Exit: deleting every auxiliary SQLite database does not change the next routing decision.

### Phase 2 — enforce admission control

- Router requests an AssistX reservation or consumes a signed routing decision.
- Per-runtime semaphores enforce slot count.
- Queue depth, timeout, cancellation, and circuit state are visible.
- Failures reduce admission rather than trigger load storms.

Exit: a single-slot endpoint survives a concurrency test and receives at most one active generation.

### Phase 3 — controlled runtime actions

- Add separate LM Studio and llama.cpp node adapters.
- Implement inspect/load/unload/restart as typed idempotent actions.
- Keep LM Studio Link as an access transport, never the identity source.
- Add memory-fit checks and post-action canaries.

Exit: repeated load requests converge on one process and target the correct physical node.

### Phase 4 — workload integration

- Hermes consumes AssistX claims and the offline gateway only.
- OpenCode is registered as a lower-priority executor.
- AI Research Vault receives an assigned endpoint/model and cannot perform independent discovery or loading.

Exit: all workloads use the same route decision and provenance chain.

### Phase 5 — retire duplicates

- Archive `auto-assign` after contracts/tests are absorbed.
- Archive `fleet-inference-configs` after launchers are merged into `fleet-llm-profiles`.
- Archive the standalone `fleet-resilience` daemon after probes are integrated.
- Remove the legacy router/assign overlay and stale deployment documentation.

## Acceptance gates

The reconciliation is not complete until these pass:

- No configured or discovered public inference provider can receive a request.
- A Link-exposed remote model is displayed under its physical runtime node, not localhost.
- A model already loaded remotely is never loaded again because of a localhost alias.
- One-slot endpoints never receive concurrent generations.
- Router restart, AssistX restart, and auxiliary SQLite deletion do not duplicate model instances or lose canonical state.
- Static profile drift is visible and cannot overwrite fresh observations.
- Hermes can execute a claimed task through the selected local model.
- OpenCode can be disabled without changing inference routing.
- AI Research Vault can run entirely against an assigned local endpoint with local embeddings.

## First implementation work items

1. Add strict offline-provider validation and physical-runtime identity to `auto-router`.
2. Add LM Studio `ps --host` observation ingestion and loaded-instance schema to AssistX.
3. Change the AssistX overlay default from `router_plus_assign` to router-only and mark auto-assign unsupported.
4. Add per-runtime slot admission and a zero-capacity default for unknown endpoints.
5. Rename the benchmark repo's `lms` console command to avoid shadowing the official LM Studio CLI.
6. Merge proven launchers into `fleet-llm-profiles` as systemd units; reject cron supervision as production-ready.
7. Add an offline-only AI Research Vault environment and remove independent endpoint discovery from production jobs.

Until those items pass, the correct operating mode is a smaller stable fleet with manually loaded LM Studio models—not a larger automatically discovered fleet that cannot identify or protect its runtimes.
