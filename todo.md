Immediate next steps (implement now)

Lock the graph schema & indexes

Requirements

Add/expand ensure_schema() to include:

(:Transcription {id}) UNIQUE

(:Segment {id}) UNIQUE

(:Task {id}) UNIQUE

(:AgentRun {id}) UNIQUE

(:ToolCall {id}) UNIQUE

(:Artifact {id}) UNIQUE

Add numeric timestamp props on all time-bearing nodes: created_at_ts, updated_at_ts (ms).

Optional: indexes on Task.status, Task.kind, Transcription.key.

DoD

Fresh DB creates constraints without error; CALL db.indexes() shows them.

Finish the Q&A pipeline endpoints

Requirements

/api/ask supports mode=sync|async|auto (already added).

/api/answers, /api/answers/{id}, /api/answers/{id}/events (SSE), WS /ws/answers (global + per-answer live) working.

Dashboard /answers renders and live-updates (WS with SSE fallback).

DoD

Manual curl + UI clicks return data; live updates flip from QUEUED→RUNNING→DONE.

Worker + queue health

Requirements

Add Compose healthchecks and depends_on: condition: service_healthy.

Metrics counters around job start/finish/fail.

DoD

docker compose ps shows healthy; /metrics exposes queue depth + success/fail counters.

Sandbox the Python analysis runner

Requirements

Execute analysis in a subprocess with:

--timeout (env: ANALYSIS_TIMEOUT_S, default 8s)

Memory limit (Linux cgroup or resource.setrlimit in child)

Permit only stdlib + pandas; deny open, os, subprocess, requests, network.

DoD

Malicious code (file I/O, sockets) is blocked; long loops are killed on timeout.

Cache + idempotency

Requirements

Keep Redis cache key = question + schema_fp.

Add optional idempotency_key to /api/ask(_async) to short-circuit duplicates.

DoD

Repeated asks with same question/schema hit cache; idempotent calls return same answer_id.

Short-term upgrades (next layer)

Observability

Requirements

Prometheus metrics:

qa_requests_total{mode,status}

qa_cypher_attempts_total

qa_duration_seconds (histogram for end-to-end & phase timings)

rq_jobs_in_queue, rq_jobs_running, rq_jobs_failed

Structured logs (JSON) with answer_id, run_id, job_id.

DoD

Grafana panel shows latency percentiles, success rate, queue depth.

Schema-aware Cypher quality

Requirements

Expand schema introspection with sample values for key props (top N) to reduce LLM errors.

Add a static “data model prompt” doc checked into repo (labels, rels, prop semantics).

DoD

Cypher repair loop succeeds within ≤2 attempts in common cases.

Similar-question fast-path

Requirements

Add embeddings on question (e.g., nomic-embed-text via Ollama or sentence-transformers).

Store in Redis (RediSearch vector) or Neo4j vector index; threshold to reuse prior answer.

DoD

Similar questions (cosine ≥ threshold) return cached answer with provenance.

Model governance

Requirements

Make OLLAMA_MODEL selectable per request.

Add fallback order & circuit breaker (e.g., try llama3.1:8b → gemma2:2b).

DoD

A model outage degrades gracefully to fallback without 5xx.

End-to-end tests

Requirements

Ephemeral Neo4j container + seed fixture (tiny dataset).

Mock LLM responses (deterministic JSON) to test:

Cypher drafting/repair

Analysis code generation/execution

Answer composition

API tests for /api/ask (sync/async/auto) and streaming endpoints.

DoD

pytest -q passes locally and in CI.

Hardening & production concerns

Security

Requirements

Replace Basic Auth with OIDC (e.g., Google/Microsoft) or a reverse-proxy auth.

Rotate secrets via env/secret manager; never log them.

CORS allowlist for your frontends; CSRF for browser POSTs.

Rate limiting on /api/ask (per IP/user), request size limits.

DoD

Unauthorized requests blocked; basic scans (ZAP) report no criticals.

Data governance

Requirements

PII classification toggles: optional redaction agent for analysis outputs and logs.

Retention: TTLs on assistx:answers:* and RQ job artifacts; Neo4j cleanup cron.

DoD

Retention documented; PII redaction switch verified.

Deployment

Requirements

Split Compose for host vs infra (already done), plus a compose.prod.yml:

Healthchecks

Restart policies

Log drivers (json-file with max size/rotate)

CI/CD pipeline (Actions): build, scan, push images; deploy to env.

DoD

One-command deploy per environment; versioned images.

Performance knobs

Requirements

Control RQ concurrency via WORKER_CONCURRENCY.

LLM token & time budgets per phase (envs).

Optional GPU support for Whisper + LLM with compose overrides.

DoD

Load test: sustained N QPS with SLOs met; worker scales horizontally.

Concrete acceptance criteria per component

/api/ask (auto) returns inline result if ready within timeout_s, else 202 with answer_id.

Neo4j logs show one AgentRun with ordered ToolCalls per answer.

Dashboard shows a new row immediately upon enqueue; status flips in <1s.

Cache hit ratio measurable; schema change invalidates cache via fingerprint.

Sandbox kills infinite loops; cannot touch filesystem or network.

Tests: at least 80% coverage of pipeline logic (excluding LLM network).

Required configuration (env)

Core: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, REDIS_URL

LLM: OLLAMA_HOST, OLLAMA_MODEL, LLM_TIMEOUT_S

Whisper: WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, TRANSCRIPTIONS_ROOT

QA: QA_CACHE_TTL_S, ANALYSIS_TIMEOUT_S, RQ_QUEUE, WORKER_CONCURRENCY

Auth: BASIC_AUTH_USER, BASIC_AUTH_PASS (or OIDC settings)

CORS: CORS_ALLOW_ORIGINS

Answers store: ANSWERS_TTL_S

Inputs I’ll assume unless you override

Default LLM: llama3.1:8b

Embedding model (when we add similarity): nomic-embed-text

Time budgets: ANALYSIS_TIMEOUT_S=8, LLM_TIMEOUT_S=180

RQ queue: assistx, concurrency = number of CPUs

If you want, I can start by:

adding the healthchecks + metrics,

hardening the analysis sandbox (subprocess+limits), and

writing pytest fixtures (ephemeral Neo4j + mocked LLM) — just say “go” and I’ll drop the code.