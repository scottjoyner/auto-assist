# Model Endpoint Registry Contract

_Last updated: 2026-05-27_

## Purpose

The model endpoint registry lets AssistX discover, benchmark, and route work to local OpenAI-compatible endpoints, LM Studio hosts, Hermes model workers, and future model servers.

For the current Paperclip cutover release, model endpoints are inventory and
advisory-drafting resources only. They do not select a Hermes worker, claim an
AssistX task, or replace Paperclip as the non-realtime execution route.

## Current draft lane decision

The MacBook Air LM Studio endpoint is the first optional draft lane:

```yaml
model_endpoint_id: scotts-macbook-air.lmstudio
node_id: scotts-macbook-air
base_url: http://100.85.64.117:1234
provider: lm_studio
network_preference: tailscale
purpose: operator-invoked low-risk drafting
preferred_model: qwen3.5-0.8b
```

Use it for small non-sensitive drafts and endpoint validation. Do not send
voice biometric data, credentials, privileged graph context, or executable
task authority through this endpoint.

On May 27, 2026, `qwen3.5-0.8b` was used temporarily in bounded synthetic
Paperclip/Hermes diagnostics to isolate integration behavior from x1 model
latency. Signed canary `ASS-28` proves that integration path and retry behavior
under a responsive model; the endpoint was restored to advisory-only status
afterward and was not promoted to automatic task execution.

---

## Endpoint schema

```yaml
model_endpoint_id: string
node_id: string
provider: lm_studio | openai_compatible | ollama | llama_cpp | vllm | other
base_url: string
models_url: optional string
health_url: optional string
status: online | degraded | offline | unknown
auth_type: none | bearer | basic | local_only
network_preference: tailscale | lan | localhost
last_probe_at: ISO-8601
```

---

## Model schema

```yaml
model_id: string
model_endpoint_id: string
served_name: string
family: qwen | llama | mistral | phi | gemma | unknown
parameter_size: optional string
context_length: optional int
quantization: optional string
loaded: boolean
supports_json: optional boolean
supports_tools: optional boolean
supports_vision: optional boolean
notes: optional string
```

---

## Benchmark schema

```yaml
benchmark_id: string
model_id: string
node_id: string
task_profile: chat | summarization | json_extraction | code_planning | cypher_generation | embedding | vision_summary
tokens_per_second: optional float
time_to_first_token_ms: optional int
latency_p50_ms: optional int
latency_p95_ms: optional int
quality_score: optional float
success_rate: optional float
measured_at: ISO-8601
prompt_sha256: optional string
```

---

## Initial routing policy

### Drafts and light summaries

Prefer low-power nodes when latency does not matter.

### Fast interactive answers

Prefer demo/demo-1 if online and benchmarked faster.

### High-context planning

Prefer x1-370 or any high-memory node.

### Legacy/continuity jobs

deathstar-XPS-8920 may continue to serve legacy ingest-adjacent model work.

---

## Probe contract

Probe these endpoints when available:

```http
GET /v1/models
GET /health
GET /api/v0/models
```

Store probe failures as health records, not fatal errors.

AssistX operator APIs for this phase:

```http
GET  /api/swarm/model-endpoints
POST /api/swarm/model-endpoints/register
POST /api/swarm/model-endpoints/{model_endpoint_id}/probe
POST /api/drafts/generate
```

All four endpoints require AssistX operator authentication. Draft generation
uses `DRAFT_MODEL_BASE_URL` and `DRAFT_MODEL_NAME`; it does not change the
global inference backend used by existing workflows.

---

## Neo4j model

```cypher
(:ModelEndpoint {model_endpoint_id, provider, base_url, status, last_probe_at})
(:Model {model_id, served_name, family, context_length, loaded})
(:BenchmarkResult {benchmark_id, task_profile, tokens_per_second, latency_p95_ms, quality_score, measured_at})
```

Relationships:

```cypher
(:SwarmNode)-[:EXPOSES]->(:ModelEndpoint)
(:ModelEndpoint)-[:SERVES]->(:Model)
(:Model)-[:HAS_BENCHMARK]->(:BenchmarkResult)
(:Capability)-[:CAN_USE_MODEL]->(:Model)
```

---

## Routing algorithm MVP

1. Filter endpoints by required capability.
2. Remove offline/degraded endpoints unless fallback needed.
3. Prefer endpoints with successful benchmark for the task profile.
4. Prefer lower-power nodes for low-priority drafting.
5. Prefer faster nodes for voice-interactive tasks.
6. Prefer data-local nodes when task inputs are large.
7. Fall back to x1-370 if no better route is available.

---

## Implementation checklist

- [x] Add model endpoint probe service.
- [x] Store `/v1/models` results.
- [x] Add authenticated registration/probe and optional draft-generation APIs.
- [ ] Add benchmark runner CLI.
- [ ] Add task-profile benchmark prompts.
- [ ] Add routing score function.
- [ ] Add dashboard panel for model endpoints.
- [ ] Add no-public-API/offline-only mode flag.
