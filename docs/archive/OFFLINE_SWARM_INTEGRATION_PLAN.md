# Offline Swarm Compute Architecture — Integration Plan

_Last updated: 2026-05-26_

## Purpose

This document extends the existing AssistX roadmap into a cross-repository integration plan for an offline/local-first compute swarm. It is intended to coordinate work across:

- `scottjoyner/auto-assist` — orchestration, command center, Neo4j memory, task dispatch, Hermes/Paperclip integration.
- `scottjoyner/Sophia` — voice sidecar, speaker verification, STT/VAD/TTS, voice-authenticated interaction.
- `scottjoyner/auto-ingest` — media/data ingestion, local dataset processing, distributed workers, Neo4j persistence for detections/transcripts/content artifacts.

AssistX should become the control plane and memory authority for the swarm. Sophia and auto-ingest should publish normalized events, capabilities, health, and artifacts into the same graph-backed operating model.

---

## Current repo baseline

### AssistX / `auto-assist`

Current README and docs show AssistX is already the orchestration command center with:

- Neo4j graph memory, orchestration state, provenance, and memory.
- Paperclip live dispatch for tasks/issues/agent routing.
- Hermes persistent agent sessions and an external memory provider.
- Voice/TTS intent capture through browser media recording.
- Legacy transcript-to-summary-to-task execution still active.
- API and UI endpoints for intents, context packets, memory, sessions, tasks, devices, dispatches, and command-center workflows.
- Phase 6 hardening/canary scripts and go-live checklist.

AssistX therefore owns the cross-system task lifecycle: `Intent -> Task/Dispatch -> AgentRun -> ToolCall -> Artifact -> MemoryItem/SignalEvent`.

### Sophia

Sophia is now a Hermes-facing voice service rooted under `voice-agent/`. It provides:

- FastAPI voice sidecar on port `8765`.
- WebRTC VAD.
- faster-whisper STT.
- SpeechBrain ECAPA-TDNN speaker verification.
- Piper / pyttsx3 TTS fallback.
- OpenAI-compatible LLM intent routing.
- Neo4j-backed voice identity, voiceprints, captures, and events.
- A voice-insight workflow for training sample export, voiceprint build, clone dataset build, and legacy segment promotion.

Sophia should become the low-latency voice/auth edge for the swarm. It should not become the global orchestration brain. Its runtime should publish voice events and authenticated intent envelopes into AssistX.

### auto-ingest

The current auto-ingest tree has evolved into a local-first ingestion and content/data workflow repository with:

- Content OS CLI for local, approval-gated content workflows.
- Local source adapters for transcripts, markdown, PDFs, CSV/JSON metadata, and manifests.
- Dockerized ingest service, ingest worker, content service, cron jobs, and NAS drop queue.
- Data-family path profiles for audio, dashcam, bodycam, legacy drops, and shared worker queues.
- A birdcam integration with API, worker, Neo4j graph repository, outbox replay, clip storage, and tests.

Auto-ingest should become the swarm's data-plane worker library: scan, normalize, transcribe, detect, summarize, package, and persist artifacts. It should push state changes to Neo4j and expose worker capability/health to AssistX.

---

## Target architecture

```text
                 User / Operator / Phone / Browser
                              |
                              v
                  Sophia voice edge / web input
                  - VAD / STT / speaker auth
                  - low-latency voice response
                  - emits normalized intent/event envelopes
                              |
                              v
+------------------------------------------------------------------+
| AssistX control plane                                             |
| - Neo4j memory and provenance graph                               |
| - task lifecycle and dispatch                                     |
| - Hermes agent loop                                               |
| - Paperclip issue/assignment bridge                               |
| - model endpoint registry                                         |
| - command center dashboard                                        |
+------------------------------------------------------------------+
                              |
                              v
+------------------------------------------------------------------+
| Offline swarm workers                                             |
| - auto-ingest media/data/content workers                          |
| - Sophia voice-insight jobs                                       |
| - LM Studio/OpenAI-compatible endpoint hosts                      |
| - GPU/CPU specialized workers                                     |
| - NAS/drop-queue and artifact storage                             |
+------------------------------------------------------------------+
                              |
                              v
                  Neo4j + filesystem artifact layer
                  - graph state is authoritative for metadata
                  - filesystem/NAS is authoritative for binaries
```

### Control-plane rule

AssistX is the orchestration source of truth. Other services may own specialized execution, but they should not define independent task state machines that cannot be reconciled back into AssistX.

### Data-plane rule

auto-ingest and Sophia may keep local SQLite/outbox state for reliability, but durable metadata, events, identities, detections, jobs, summaries, dispatches, and outcomes should reconcile into Neo4j.

### Offline-first rule

All critical operations should work over LAN/Tailscale/local DNS without depending on public cloud services. Public APIs may be optional providers, not mandatory control paths.

---

## Shared contracts to create next

### 1. Swarm node registry

Create a graph-backed registry for every participating host:

```cypher
(:SwarmNode {
  node_id,
  hostname,
  tailscale_ip,
  lan_ip,
  os,
  arch,
  roles,
  gpu,
  cpu_threads,
  memory_gb,
  storage_profile,
  status,
  last_seen_at
})
```

Relationships:

```cypher
(:SwarmNode)-[:EXPOSES]->(:ServiceEndpoint)
(:SwarmNode)-[:CAN_RUN]->(:Capability)
(:SwarmNode)-[:HOSTS_MODEL]->(:ModelEndpoint)
(:SwarmNode)-[:MOUNTS]->(:StorageRoot)
(:SwarmNode)-[:REPORTS]->(:HealthCheck)
```

Minimum endpoint families:

- AssistX API.
- Sophia voice sidecar.
- Neo4j.
- LM Studio/OpenAI-compatible `/v1` endpoints.
- auto-ingest worker APIs or queue runners.
- Birdcam/vision APIs.
- Redis or queue broker, if retained.

### 2. Capability contract

Every worker should advertise capabilities in a common schema:

```yaml
capability_id: stt.whisper.local
kind: stt | tts | llm | embedding | vision | ingest | graph | file | planning | qa
runtime: docker | host | wsl | macos | windows | linux
inputs:
  - audio/wav
  - audio/mp3
outputs:
  - transcript/json
  - transcript/srt
cost_profile:
  cpu: medium
  gpu: optional
  memory_gb_min: 4
  storage: sequential_read_heavy
safety:
  requires_approval: false
  pii_possible: true
health:
  command: string
  endpoint: optional
```

AssistX should match tasks to nodes based on this contract, not hard-coded host names.

### 3. Unified event envelope

All repos should emit a shared event envelope:

```json
{
  "event_id": "uuid-or-deterministic-key",
  "event_type": "voice.auth.decision | ingest.file.seen | vision.detection.created | task.completed",
  "source_repo": "Sophia | auto-ingest | auto-assist",
  "source_service": "voice-agent | ingest-worker | assistx-api",
  "node_id": "hostname-or-registered-node-id",
  "occurred_at": "ISO-8601",
  "idempotency_key": "stable replay key",
  "subject": {
    "kind": "file | utterance | detection | task | model | endpoint",
    "id": "source-specific-id"
  },
  "payload": {},
  "artifact_refs": [],
  "privacy": {
    "pii": true,
    "retention_class": "private-local"
  }
}
```

### 4. Task dispatch contract

AssistX should expose the canonical task API for workers:

- `GET /api/agent/tasks?capability=...`
- `POST /api/tasks/{task_id}/claim`
- `POST /api/tasks/{task_id}/heartbeat`
- `POST /api/tasks/{task_id}/complete`
- `POST /api/tasks/{task_id}/fail`
- `POST /api/tasks/{task_id}/artifact`

Workers should be able to run disconnected for a short period by writing local outbox records, then replaying completions and artifact metadata.

### 5. Model endpoint registry

Create a service that probes each Tailscale/LAN host for OpenAI-compatible endpoints:

- `/v1/models`
- LM Studio local API model list, when available.
- Health probe.
- Context length / loaded model / provider metadata where discoverable.
- Benchmark result per model, per host, per task profile.

Persist as:

```cypher
(:ModelEndpoint)-[:RUNS_ON]->(:SwarmNode)
(:ModelEndpoint)-[:SERVES]->(:Model)
(:Model)-[:HAS_BENCHMARK]->(:BenchmarkResult)
(:Capability)-[:CAN_USE_MODEL]->(:Model)
```

### 6. Storage and artifact contract

Metadata goes to Neo4j. Binary payloads stay on mounted storage.

Canonical artifact metadata:

```yaml
artifact_id: deterministic-or-uuid
kind: audio | video | image | transcript | detection_csv | clip | report | model_manifest
uri: file:///nas/... or relative storage root path
sha256: optional-but-preferred
size_bytes: number
created_at: ISO-8601
producer_task_id: optional
source_event_id: optional
retention_class: ephemeral | keep | protected | evidence
```

---

## Next implementation sequence

### Phase A — Contract freeze and docs alignment

1. Add this document to all three repos.
2. Confirm ownership boundaries:
   - AssistX owns orchestration and graph contracts.
   - Sophia owns voice edge/auth/TTS behavior.
   - auto-ingest owns media/data workers and local artifact generation.
3. Answer the design-decision questions in the bottom section.
4. Convert answered decisions into `docs/swarm_contracts/*.md` files.

Exit criteria:

- Shared vocabulary accepted.
- No repo has conflicting definitions of task state, event envelope, node identity, or artifact metadata.

### Phase B — Swarm inventory MVP

1. Add `swarm_registry` module in AssistX.
2. Add `SwarmNode`, `ServiceEndpoint`, `Capability`, `ModelEndpoint`, `StorageRoot`, and `HealthCheck` schema constraints.
3. Add a CLI/API to register/update hosts.
4. Add read-only dashboard panel for node health and capabilities.
5. Add simple host probe scripts for LM Studio, Sophia, auto-ingest, Neo4j, and AssistX.

Exit criteria:

- Every major machine can be registered.
- AssistX can show last-seen status and capabilities.
- Model endpoints can be discovered without manually editing code.

### Phase C — Event ingestion and outbox replay

1. Implement `POST /api/events` in AssistX for the unified event envelope.
2. Add HMAC/shared-token authentication per service.
3. Add event idempotency by `event_id` or `idempotency_key`.
4. Add local outbox client libraries in Sophia and auto-ingest.
5. Reconcile events into Neo4j as `SignalEvent` plus typed nodes/edges.

Exit criteria:

- Sophia emits voice/auth/capture events.
- auto-ingest emits file seen, transcript complete, detection created, and artifact created events.
- Replaying an outbox twice does not create duplicate graph state.

### Phase D — Task routing and worker claims

1. Normalize task capability requirements in AssistX.
2. Teach auto-ingest workers to poll AssistX for jobs by capability.
3. Teach Sophia voice-insight jobs to claim auth/model-training tasks.
4. Preserve NAS drop jobs as a compatibility worker backend, but report state to AssistX.
5. Add retry, heartbeat, lease expiry, and cancellation behavior.

Exit criteria:

- AssistX can dispatch a transcript job to auto-ingest.
- AssistX can dispatch a voiceprint rebuild or auth-calibration job to Sophia.
- Failed/offline workers release work safely.

### Phase E — Voice-authenticated quick input

1. Sophia receives voice input and performs VAD/STT/speaker verification.
2. Sophia emits an authenticated quick-input envelope to AssistX.
3. AssistX retrieves memory/context, plans work, and dispatches if needed.
4. Sophia receives a response payload and speaks it using configured TTS voice policy.
5. Persist `VoiceAuthDecision -> UserIntent -> AgentRun -> ToolCall/Artifact` trace.

Exit criteria:

- Scott-authenticated path can trigger approved local actions.
- Unknown speaker path can receive a response while remaining permission-limited.
- Operator can audit the full trace.

### Phase F — Model routing and benchmarking

1. Probe all LM Studio/OpenAI-compatible hosts.
2. Benchmark task profiles:
   - short chat
   - summarization
   - JSON extraction
   - Cypher generation
   - code edit planning
   - vision metadata summarization, where applicable
3. Store results in Neo4j and optional CSV/SQLite sidecars.
4. Route AssistX/Hermes tasks to best available endpoint by profile.

Exit criteria:

- AssistX chooses a model endpoint based on availability and benchmark metadata.
- Fallback behavior is defined when a node is offline.

### Phase G — End-to-end offline swarm demo

Demo scenario:

1. Voice request enters Sophia from phone/browser.
2. Sophia authenticates speaker and sends quick input to AssistX.
3. AssistX plans the work and dispatches a media/data task to auto-ingest.
4. auto-ingest processes a local file or stream and writes artifacts + graph events.
5. AssistX updates the command center and sends a response back through Sophia.
6. All events, tasks, tool calls, artifacts, and memory updates are auditable in Neo4j.

Exit criteria:

- The workflow succeeds with public internet disabled, assuming LAN/Tailscale and local services are available.

---

## Design-decision questions for Scott

### A. Control plane and source of truth

1. Should AssistX be the only task-state authority, or should auto-ingest keep an independent task state and synchronize summaries back?
2. Should Neo4j be a single shared database with labels/namespaces, or separate databases such as `assistx`, `memory`, `ingest`, and `voice`?
3. Do you want one command center UI in AssistX, or separate UIs with AssistX linking out?
4. Should Paperclip remain in the core dispatch path, or become an optional human/project-management mirror?
5. Should Redis remain the queue, or should the swarm standardize on Neo4j task polling plus local outboxes?

### B. Host and network topology

6. Which host is the first production control-plane machine: x1-370, mini-pc-22, Beelink, deathstar-XPS-8920, or another box?
7. Should Tailscale names/IPs be treated as canonical node addresses?
8. Should LAN IPs be preferred when reachable and Tailscale used as fallback?
9. Should any service require inbound port forwarding, or should everything work through local/Tailscale-only paths?
10. Do you want a local DNS/reverse proxy layer with names like `assistx.local`, `neo4j.local`, `sophia.local`, `models.local`?

### C. Security and identity

11. For voice input, should Scott-authenticated sessions be allowed to execute actions immediately, or should high-impact actions still require UI approval?
12. What should unknown speakers be allowed to do: ask questions only, submit notes, trigger no actions, or use a restricted guest policy?
13. Should Sophia always respond in Scott clone voice, even for unknown speakers, as currently planned?
14. Should every service-to-service event require HMAC signing, mTLS, Tailscale identity, or a simpler shared token for now?
15. What is the retention policy for raw voice clips used for authentication and clone training?

### D. Data and storage

16. What are the canonical storage roots for audio, dashcam, bodycam, birdcam, transcripts, and generated artifacts?
17. Should artifact paths be stored as absolute host paths, container paths, storage-root-relative paths, or all three?
18. Which artifacts are protected/evidence-grade and should never be auto-pruned?
19. Should auto-ingest continue using NAS drop jobs, or should AssistX task polling replace the drop queue over time?
20. Should SQLite be allowed only for outbox/cache state, with Neo4j always authoritative for metadata?

### E. Model routing and compute

21. Which machines should run LM Studio or OpenAI-compatible model servers initially?
22. Should the benchmark registry prefer fastest model, highest quality model, or cheapest/local-lightest model by task type?
23. Should GPU-heavy jobs be scheduled explicitly, or should workers self-select based on capability advertisements?
24. Should code-editing/planning agents run on a different model profile than summarization/extraction agents?
25. Should there be a hard offline mode that refuses public API fallback?

### F. Implementation order

26. What is the first end-to-end demo you want: voice command, media ingest, birdcam event, transcript summarization, or model endpoint routing?
27. Which repo should get the first runtime change after this planning pass?
28. Do you want PRs per repo, or a single branch pushed directly to each default branch during this early phase?
29. Should we prioritize schema contracts and tests before UI work?
30. What is the minimum “working swarm” definition for the next milestone?

---

## Proposed immediate next actions after Scott answers

1. Convert answered decisions into `docs/swarm_contracts/decisions.md`.
2. Add `docs/swarm_contracts/event_envelope.md`.
3. Add `docs/swarm_contracts/node_registry.md`.
4. Add `docs/swarm_contracts/task_lifecycle.md`.
5. Add a first implementation issue list per repo.
6. Implement the smallest end-to-end demo path selected in question 26.
