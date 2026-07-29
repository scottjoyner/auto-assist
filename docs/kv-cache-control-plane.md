# KV-cache control plane

AssistX catalogs reusable prompt-prefix caches and routes compatible work
toward them. Neo4j stores identities, compatibility, location, expiry, and
telemetry. Raw prompts, token arrays, and KV tensors remain outside the graph.

## Architecture

```text
trusted prompt producer
  -> keyed digest of token IDs
  -> task payload with opaque prefix_id and compatibility fingerprint
  -> allocator compares node/model/cache economics
  -> atomic node/model/cache reservation
  -> node-local runtime adapter resolves or restores cache
  -> inference
  -> signed-in node records hit/miss/restore telemetry
```

This design supports affinity before cache transfer exists. An LM Studio or
vLLM node can earn a placement bonus for a resident compatible prefix without
exporting it. llama.cpp and SGLang may additionally use an explicitly
configured node-local adapter.

## Graph records

```text
(:PromptPrefix {prefix_id, token_count, privacy_scope, scope_id, last_used_at_ts})
(:KVCacheManifest {
  cache_id, model_id, model_quantization,
  kv_k_quantization, kv_v_quantization,
  runtime, runtime_version, compatibility_fingerprint,
  node_id, endpoint_id, token_count, bytes,
  storage_tier, portable, status, expires_at_ts,
  hit_count, miss_count
})
(:KVCacheEvent {id, outcome, node_id, task_id, tokens_saved, prefill_ms_saved})
```

Relationships:

```text
(:KVCacheManifest)-[:CACHE_FOR]->(:PromptPrefix)
(:KVCacheManifest)-[:RESIDENT_ON]->(:SwarmNode)
(:KVCacheManifest)-[:SERVED_BY]->(:ModelEndpoint)
(:KVCacheManifest)-[:CACHE_OF]->(:Model)
(:KVCacheManifest)-[:HAS_EVENT]->(:KVCacheEvent)
```

The external `artifact_ref` may identify runtime-controlled storage but is
removed from catalog/status responses.

## Prefix identity

`assistx.kv_cache.prefix_digest()` creates:

```text
HMAC-SHA256(secret, schema + privacy scope + scope ID + token IDs)
```

The result is an opaque `prefix-<64 hex>` identifier. Prompt text and token IDs
must not be written to Neo4j or logs. Use a separate
`ASSISTX_KV_PREFIX_HMAC_SECRET`, rotate it as a cache-generation boundary, and
expect old prefix identities to miss after rotation.

Privacy scopes:

- `private`: one agent/user/session scope;
- `project`: explicitly shared within one project;
- `fleet`: non-sensitive static material approved for all nodes.

Both `privacy_scope` and exact `scope_id` must match for reuse.

## Compatibility fingerprint

Cache reuse is rejected unless the full fingerprint matches:

- model artifact hash and served model ID;
- weight quantization;
- K- and V-cache quantization;
- tokenizer and chat-template hashes;
- adapter/LoRA hash;
- runtime, runtime version, and cache-format version;
- context length and RoPE configuration.

Friendly model names are insufficient. Two GGUF quantizations of the same model
must produce different fingerprints.

## Task contract

A trusted producer can add this to a task payload:

```json
{
  "kv_cache": {
    "prefix_id": "prefix-<64 lowercase hex>",
    "compatibility_fingerprint": "<64 lowercase hex>",
    "privacy_scope": "project",
    "scope_id": "auto-assist",
    "affinity_node_id": "xwing"
  }
}
```

The allocator reports `cache_mode`, prefill/restore seconds, seconds saved,
cache locality bonus, and session-affinity bonus for every candidate.
Reservation persists the chosen `cache_id` with the node and model.

Cache value is bounded so it cannot override health, capability, operator
control, or measured-quality constraints.

## AssistX APIs

Operator catalog:

```text
GET /api/fleet/kv-cache
GET /api/fleet/kv-cache?active_only=true
```

Trusted node writes:

```text
POST /api/fleet/kv-cache/manifests
POST /api/fleet/kv-cache/events
```

Node writes require Basic/trusted-header authentication plus the node-specific
`X-Fleet-Node-Token`. A node can update events only for cache IDs cataloged on
that same node. Cache ID ownership, prefix identity, and compatibility
fingerprint are immutable after first registration.

Manifest status expires lazily from `READY`/`RESTORING` to `EXPIRED`.
`EVICT` events set `EVICTED`. Events support `HIT`, `MISS`, `RESTORE`, and
`EVICT`.

## Node-local adapter contract

Set:

```env
FLEET_KV_CACHE_RUNTIME=llama_cpp
FLEET_KV_CACHE_CONTROL_URL=http://127.0.0.1:8099
```

The adapter must be local or mutually authenticated. AssistX calls:

```text
POST {FLEET_KV_CACHE_CONTROL_URL}/v1/kv-cache/resolve
```

Request:

```json
{
  "schema_version": 1,
  "node_id": "xwing",
  "model_id": "model-id",
  "prefix_id": "prefix-...",
  "compatibility_fingerprint": "...",
  "privacy_scope": "project",
  "scope_id": "auto-assist",
  "preferred_cache_id": "cache-id-or-null"
}
```

Response:

```json
{
  "mode": "local",
  "cache_id": "cache-id",
  "compatibility_fingerprint": "...",
  "tokens_saved": 12000,
  "prefill_ms_saved": 42000,
  "restore_ms": 0,
  "request_fields": {
    "slot_id": 2,
    "cache_prompt": true
  },
  "manifest": {}
}
```

Only `cache_id`, `cache_prompt`, `session_id`, and `slot_id` may pass from the
adapter into the inference request. The adapter cannot override model,
messages, generation limits, target URL, or credentials. A returned
compatibility fingerprint must equal the task fingerprint.

If the adapter is absent or rejects the request, ordinary inference continues
without cache request fields. This is a performance degradation, not an
availability failure.

## Runtime capability matrix

| Runtime | Affinity | Export/restore | Distributed restore | Integration |
|---|---:|---:|---:|---|
| LM Studio | yes | no | no | loaded-instance affinity and KV configuration |
| llama.cpp | yes | adapter-gated | no | slot save/restore sidecar |
| vLLM | yes | no | no | automatic prefix-cache affinity and metrics |
| SGLang | yes | adapter-gated | adapter-gated | RadixAttention/HiCache |
| unknown OpenAI-compatible | no | no | no | fail closed |

Node reports can narrow known capabilities but cannot promote an unknown
runtime into export or distributed-restore authority.

## Allocation economics

The allocator estimates:

```text
prefill seconds = cached prefix tokens / measured prefill tokens per second
restore seconds = cache bytes / measured transfer throughput
seconds saved = max(0, prefill seconds - restore seconds)
```

The score bonus is capped at `0.18`; session affinity adds at most `0.04`.
Operations displays the selected mode and estimated savings. Prometheus exports
event counts, estimated prefill milliseconds saved, and restore latency.

## Rollout

1. Configure a distinct prefix-HMAC secret on trusted prompt producers.
2. Enable manifest reporting on one node without a control endpoint.
3. Verify strict model/quant/template fingerprint mismatches produce misses.
4. Add `kv_cache` identity to one stable, non-sensitive system/tool prefix.
5. Observe affinity decisions and measured hit rate.
6. Deploy a loopback-only llama.cpp or SGLang adapter.
7. Verify expiry, eviction, restart, invalid signature/token, and wrong-scope
   behavior.
8. Expand only when measured prefill savings exceed restore and retention cost.

Do not begin with private conversation history. Stable system instructions,
tool schemas, repository policies, and approved project context are safer
high-reuse prefixes.

## Failure and security rules

- Never accept raw prompt text or token arrays in a cache manifest.
- Never reuse across privacy scope or scope ID.
- Never infer compatibility from model name.
- Never let an adapter broaden its known runtime capability.
- Never expose storage locators in list/status responses.
- Expired or evicted manifests cannot influence routing.
- A cache miss must fall back to ordinary inference.
- Cache timing is potentially sensitive; cross-user caches require explicit
  fleet-safe classification.
