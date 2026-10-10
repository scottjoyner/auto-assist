# Fleet-wide AssistX trace inventory: evidence and storage-neutral design

Status: 2026-10-10, proposal / read-only discovery. NO database, cache, NAS data, production trace server or fleet agent has been migrated or modified.

## Measured read-only pilot

Ran scripts/inventory_trace_headers.py against the bounded local sources:
- /home/scott/git/fleet-traces (historical JSONL handoffs)
- /home/scott/git/auto-assist/artifacts (historical exported OpenCode sessions)
- Limits: 70 files, 4 MB input, 40,000 rows; output is counters only, not content.

2026-10-10 observation: **13 files, 2,003,953 bytes scanned, 886 JSONL rows, 150 distinct session identifiers, ~24.84 ms local wall time**. 389 rows carry session IDs, 51 carry parent-session IDs, 392 carry provider/model metadata, 389 carry token-use fields; 883 carry timestamps. **Zero rows in this pilot have trace_id, span_id or node_id**. The sample is not the full fleet and is not a credible fleet-wide volume or latency projection. No conclusion about Redis, PostgreSQL, SQLite, ClickHouse or object storage follows from this sample.

**Key blocker:** robust cross-machine search is impossible until trace/span/node identifiers and source authority are recorded consistently. A trace collection of session JSONL alone is insufficient to prove full fleet execution lineage.

## User-facing information architecture

1. Fleet (one entry per hardware node from the dedicated fleet hardware registry): observed/expected node identity, last verified, evidence link; never use ephemeral IP address as authoritative identity.
2. Sessions: agent/LLM/UI runtime, model/provider, start/end/heartbeat, node, origin, parent/root session, git revision, output status, budget and cost.
3. Traces: end-to-end trace ID with explicit custody, invocation source, spans (commands, prompt assembly, model request, tool calls, retries, approval decisions), tree and clock-corrected timeline.
4. Artifacts: raw trace reference, storage location/URI, SHA-256, byte length, owner, retention class, evidence of access, immutable manifest generation.
5. Fleet Search: text search against **approved redacted metadata only**, filters by node, provider, model, session, trace, git SHA, time range, execution type, error class, workflow, status and approval/rejection, with pagination and stable cursors. Raw sensitive payloads remain opt-in and separately permissioned.

The operator should be able to navigate node → sessions → trace tree → tool/worker spans → custody artifacts and return to the original work issue and git revision. A durable record that a span is absent/unknown is preferable to fabricated lineage.

## Proposed logical entities, not a database selection

- FleetNode(node_id, hardware_registry_ref, last_seen_at, identity_source)
- AgentSession(session_id, root_session_id, parent_session_id, node_id, runtime, user_scope, started_at, ended_at, status, model_ref, provider_ref, git_sha, source_confidence)
- Trace(trace_id, owning_session_id, generated_at, received_at, trace_schema, origin, provenance)
- Span(trace_id, span_id, parent_span_id, node_id, session_id, operation_class, started_at, ended_at, result, error_code, resource_budget)
- TraceEvent(trace_id, span_id, sequence, event_at, ingested_at, event_type, safe_metadata, artifact_ref)
- Artifact(artifact_id, sha256, byte_length, content_type, custody_receipt, retention_class, immutable_source_ref, encryption_class)
- IngestCheckpoint(node_id, producer_id, source_file_identity, offset, checksum, last_ack_time)
- PolicyDecision(trace_id, span_id, requesting_principal, capability, decision, approving_principal, approval_receipt, timestamp)

Keys and provenance must be enforced by the producer, not inferred from arbitrary model text. Preserve separate event_at, observed_at and received_at; clock skew and missed heartbeats must be visible. Encode never-observed fields as UNKNOWN.

## Measurement matrix before selecting a storage backend

- Source and fleet coverage: expected producers, reachable producers, sampled node/session IDs, missing producers and clock correction quality.
- Arrival rates: sessions/hour, spans/second (mean/p95/p99 burst), average and p99 metadata bytes, raw payload sizes, duplication/retry rate.
- Query demand: time-window filtered node search, root-session subtree expansion, latest errors, user full-text search, pagination throughput and p95 latency.
- Storage pressure: bytes per day in each retention tier, free-space high/low watermarks, WAL/checkpoint overhead, cold archive compression and backup cost.
- Failure behavior: offline node replay, duplicate event idempotency, clock skew, corrupt file handling, host compromise, ledger truncation, restore.
- Permissions: owner-scoped access, PII/sensitive prompt redaction, raw trace opt-in, immutable evidence and deletion/retention compliance.

Candidate architectures to benchmark as alternatives: embedded SQLite metadata index; PostgreSQL relational metadata store; columnar/search database for higher ingest volume; compressed object/JSONL archives with a separate index; optional Redis as **rebuildable ephemeral query cache**, not as source-of-truth audit custody. Make this decision only after at least two representative workloads and explicit disk-budget measurements.

## Safe next experiment

1. Extend every producer to emit stable event, trace, span and node IDs with source authentication and versioned envelopes (no new raw collection by default).
2. Pilot on 2–3 distinct hardware nodes and at least two runtimes (AssistX/OpenCode/Hermes), each with an explicit source manifest.
3. Run the read-only scanner and per-node clock/lineage completeness checks; reconcile indexed counts against producer and artifact ledgers.
4. Build disposable competing metadata-only indexes against a synthetic+redacted witness corpus, benchmark query p95 and on-disk bytes. Never point destructive migrators or heavy indexers at live NAS/Beelink.
5. Put a fleet search UI behind owner authentication, filter presets, pagination and payload preview opt-in; bind each visual result to a custody receipt.

## Follow-up: producer identity and cross-node pilot (2026-10-10)

The storage-neutral scripts/trace_metadata_contract.py validates versioned, explicitly allowlisted event metadata: stable event, session, trace, span, producer and node identifiers, event timestamp, operation, sequence, status, optional parent references, and bounded digests. Unknown fields are rejected, including raw prompts, coordinates and unapproved payload objects. The validator detects replay versus conflicting duplicate event IDs and counts missing parent spans within the **same trace**, even across nodes. It is not a source-authentication or custodial integrity implementation; each producer must be independently authenticated before index admission, and the verifier reports this honestly as unverified.

Local synthetic contract test: 4/4 passing; the bounded input scanner: 2/2 passing. These tests do not establish real multi-node trace production. Existing AssistX read-only CASS session search remains an optional, non-authoritative observational search path; the new contract does not choose CASS or its persistent index as canonical custody.

Two reachable fleet nodes were also checked read-only from x1-370: xwing (about 91,731,416 KiB free on /home filesystem) and Beelink (about 216,491,180 KiB free on /home filesystem). The specific fleet-traces path from the local historical sample was unavailable on both. One named Deathstar SSH probe timed out. These are host/home observations, **not NAS filesystem free-space measurements**, and they cannot establish broader fleet trace completeness, hardware identity authority, or total storage runway.

Release criteria: no unstated coverage; no coordinate/prompt leakage; every displayed trace linked to a real source receipt; explicit storage budgets and failure tests; read-only/no-overlap experiment; stakeholder selection after measurement.
