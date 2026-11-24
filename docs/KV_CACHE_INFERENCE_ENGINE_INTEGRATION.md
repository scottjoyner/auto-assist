# KV-Cache DB Layer — Integration into the Inference Engine

Status: design + partial implementation (2026-09-13).
Companion code: `src/assistx/fleet.py` (`registry_from_neo4j_rows`,
`discover_neo4j`), `src/assistx/neo4j_client.py` (endpoint/runtime/capability
readers), `src/assistx/kv_cache.py`, `src/assistx/allocation_engine.py`.

## Problem

The inference engine (allocation) historically discovered runtimes only from the
auto-router's `/v1/models`, parsing ids of the form `<runtime>-<node>.<model>`.
Any runtime/endpoint that the router never advertised (llama.cpp, vLLM, SGLang,
FastFlowLM on arbitrary ports) was invisible to allocation, even though it was
already registered in Neo4j's durable authority (`ServiceEndpoint`,
`ModelEndpoint`, `RuntimeInstance`, `FleetNode`, `Capability`, `AccessPath`,
`Model`, `LoadedModelInstance`).

At the same time, the KV-cache layer (`KVCacheManifest` / `KVCacheEvent` /
`PromptPrefix`) was already persisted in Neo4j and consulted by allocation via
`cache_match`/`cache_value` — but only for the (lmstudio-only) node set, and
without the portable-checkpoint fallback tier.

## Neo4j data model (durable authority)

| Label | Key fields | Role |
|---|---|---|
| `FleetNode` | node_id, dns_name, tailscale_ips_json, os_family, online, active, roles_json, capabilities_json, discovery_source, worker_mode, max_concurrent, energy_class | tailnet inventory |
| `RuntimeInstance` | runtime_instance_id, node_id, runtime_kind, runtime_version, headless, admitted, status, expires_at_ts | which runtime is live on a node |
| `ServiceEndpoint` / `ModelEndpoint` | endpoint_id, node_id, model_endpoint_id, service_type, provider, base_url, auth_type, network_preference, purpose, status | reachable model endpoints |
| `Capability` | capability_id, node_id, kind, name, status, requires_gpu | what a node can do |
| `AccessPath` | runtime_instance_id, base_url, transport, preference, approved, expires_at_ts | approved access paths |
| `Model` / `LoadedModelInstance` / `ModelArtifact` | model_id, provider, provider_model_id, context_length, quantization, artifact_fingerprint, capabilities_json, admitted | model identity + load state |
| `KVCacheManifest` | cache_id, node_id, endpoint_id, model_id, prefix_id, storage_tier, portable, compatibility_fingerprint, runtime, runtime_version, token_count, bytes, status, expires_at_ts, hit_count, miss_count, capabilities_json, compatibility_json | KV cache durable record |
| `KVCacheEvent` | id, cache_id, node_id, prefix_id, outcome, tokens_saved, prefill_ms_saved, restore_ms | cache hit/miss telemetry |
| `PromptPrefix` | prefix_id, token_count, privacy_scope, scope_id | prefix identity |
| `ContextPacket` | id, query, query_hash, include_sources, max_items | portable context-packet fallback |

Relationships: `KVCacheManifest -[:CACHE_FOR]-> PromptPrefix`,
`-[:RESIDENT_ON]-> SwarmNode`, `-[:SERVED_BY]-> ModelEndpoint`,
`-[:CACHE_OF]-> Model`, `-[:HAS_EVENT]-> KVCacheEvent`.

## Four-tier cache fallback (inference engine)

Allocation should consult, in priority order, mirroring the portable checkpoint
fallback in `prompt-cache-control`:

1. **Local KV** — `KVCacheManifest` on the same node, status READY/RESTORING,
   not expired, compatible fingerprint → `cache_match` mode `local`.
2. **Remote/SSD KV** — portable manifest, `storage_tier=distributed`,
   `distributed_restore` capability → mode `restore`.
3. **Context packet** — `ContextPacket` for the same prefix/scope → partial
   prefill bonus (smaller than a full KV hit).
4. **Full recompute** — miss, no bonus.

Tiers 1–2 are implemented in `kv_cache.cache_match`/`cache_value` and wired in
`allocation_engine.build_allocation_plan` via the `cache_manifests` parameter
(sourced from `neo.list_kv_cache_manifests` in `api.py`). Tier 3 is a
follow-up: add a `context_packets` parameter and a `context_packet_bonus`
computed analogously to `cache_value` but with a lower cap.

## Discovery changes

`fleet.discover()` keeps the router as the primary, fast source. After router
parsing it now best-effort enriches the registry from Neo4j when `NEO4J_URI`
is set, via `discover_neo4j()`, which calls the new read methods on
`Neo4jClient` (`list_fleet_nodes`, `list_runtime_instances`,
`list_service_endpoints`, `list_model_endpoints`) and folds them in through the
pure helper `registry_from_neo4j_rows`. Enrichment is additive and never
overwrites router-discovered entries; failures are swallowed so discovery keeps
working offline.

Non-model service endpoints are recorded (node ensured) but do not fabricate
model entries, avoiding noise in `model_map`.

## Configuration / rollout

- Set `NEO4J_URI` (already present in production `.env`) to enable enrichment.
- `ASSISTX_NEO4J_DISCOVERY` is reserved for an explicit on/off switch; until
  added, presence of `NEO4J_URI` gates it.
- Canary validation: `scripts/deploy-e2e-canary.sh` with
  `CANARY_MANAGED_NEO4J=false` connects the canary to the production Neo4j
  (`host.docker.internal:7688`) and exercises the `observe`+`cache` stages,
  including manifest registration. Last run: PASS (2026-11-14,
  `artifacts/deployment-canary/20261114T144328Z`).

## Tests

- `tests/test_fleet_discovery_parsing.py` — router-side runtime-agnostic parsing.
- `tests/test_fleet_neo4j_discovery.py` — pure conversion helper.
- `tests/test_fleet_node_kv_cache.py` — kv-cache matching.

## Follow-ups (not yet implemented)

1. Add `context_packets` tier to `cache_match`/`cache_value` and thread it
   through `build_allocation_plan` + `api.py`.
2. Add `list_context_packets` to `Neo4jClient` and a `context_packet_bonus`.
3. Make `discover_neo4j` also ingest `Capability` (capabilities list on nodes)
   and `AccessPath` (approved base URLs) so allocation's capability gating and
   endpoint selection use the durable authority.
4. Add an explicit `ASSISTX_NEO4J_DISCOVERY` env switch and a periodic refresh
   in the background thread.
