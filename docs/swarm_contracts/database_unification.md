# Database Unification Contract

_Last updated: 2026-05-26_

## Purpose

This document defines the operational database boundaries for the offline swarm architecture.

The intent is to avoid schema drift and conflicting ownership between:

- historical memory
- orchestration state
- voice/auth state
- ingestion state
- runtime cache state

---

## Canonical database roles

| Database | Role | Authority Level |
|---|---|---|
| `neo4j` | Unified Scott historical memory graph | authoritative for long-term memory |
| `assistx` | Control-plane/orchestration graph | authoritative for task state |
| `memory` | Transitional Sophia voice/auth staging graph | temporary / migratory |
| SQLite local DBs | outbox/cache/claim bookkeeping | operational only |
| Redis/FalkorDB | future cache/working-set graph | non-authoritative |

---

## `neo4j` database

This database stores durable Scott memory and provenance.

### Allowed node families

```text
Transcription
Segment
Utterance
Speaker
GlobalSpeaker
Entity
MemoryFact
Preference
Opinion
ArtifactRef
DashcamTrip
BodycamSession
BirdDetection
Observation
LocationVisit
SourceMedia
```

### Allowed relationships

```text
HAS_SEGMENT
HAS_UTTERANCE
SPOKEN_BY
MENTIONS
DERIVED_FROM
LOCATED_AT
CAPTURED_DURING
SAME_PERSON
HAS_EMBEDDING
GENERATED_FROM
```

### Required properties

All memory-bearing nodes should eventually support:

```yaml
source_repo: string
source_event_id: optional
created_at: ISO-8601
updated_at: ISO-8601
confidence: optional float
retention_class: keep | protected | evidence | ephemeral
embedding_model: optional
embedding_dimension: optional
```

---

## `assistx` database

This database stores orchestration/control-plane state.

### Allowed node families

```text
Task
Dispatch
AgentRun
ToolCall
ApprovalRequest
PolicyDecision
SwarmNode
ServiceEndpoint
Capability
ModelEndpoint
HealthCheck
TaskLease
ExecutionArtifact
```

### Required guarantees

- authoritative task lifecycle
- idempotent event reconciliation
- heartbeat visibility
- delegation traceability
- replay-safe updates

### Required task states

```text
queued
claimed
running
blocked
awaiting_approval
completed
failed
cancelled
```

### Required task properties

```yaml
task_id: string
created_at: ISO-8601
updated_at: ISO-8601
priority: low | normal | high | critical
risk_level: low | medium | high
requested_capabilities: list[string]
created_by: system | scott | registered_user
approval_required: boolean
assigned_node_id: optional
```

---

## `memory` database

Sophia currently uses this as a voice/auth memory layer.

### Current status

Transitional.

### Preferred future direction

Dual-write or migrate into unified `neo4j` memory graph.

### Acceptable short-term use

- temporary voice staging
- auth calibration state
- rapid iteration during migration

### Not acceptable long-term

- isolated long-term Scott memory inaccessible from AssistX retrieval
- divergent entity identity graphs
- conflicting embeddings or semantic search state

---

## Migration strategy

### Phase 1

Leave existing DBs operational.

Add:

- source provenance
- migration metadata
- cross-db identifiers

### Phase 2

Add dual-write support from Sophia:

```text
memory -> memory + neo4j
```

### Phase 3

Backfill embeddings into unified memory graph.

### Phase 4

Migrate or reconcile historical Sophia voice/auth records.

### Phase 5

Reduce reliance on isolated `memory` DB if no longer needed.

---

## Embedding standard

Initial preferred embedding family:

```text
sentence-transformers/all-MiniLM-L6-v2
384 dimensions
cosine similarity
```

Future evaluation candidates:

- nomic embeddings
- bge-large
- multilingual embeddings
- byte-level embeddings
- multimodal embeddings

### Required vector metadata

```yaml
embedding_model: string
embedding_dimensions: int
embedding_created_at: ISO-8601
embedding_source_text_sha256: optional
```

---

## Required Neo4j constraints

### `neo4j`

```cypher
CREATE CONSTRAINT memoryfact_id IF NOT EXISTS
FOR (n:MemoryFact)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT artifactref_id IF NOT EXISTS
FOR (n:ArtifactRef)
REQUIRE n.artifact_id IS UNIQUE;
```

### `assistx`

```cypher
CREATE CONSTRAINT task_id IF NOT EXISTS
FOR (n:Task)
REQUIRE n.task_id IS UNIQUE;

CREATE CONSTRAINT swarmnode_id IF NOT EXISTS
FOR (n:SwarmNode)
REQUIRE n.node_id IS UNIQUE;
```

---

## Anti-patterns to avoid

Do NOT:

- store authoritative task state in Redis only
- create separate entity graphs per repo
- duplicate embeddings without provenance
- assume one filesystem path works on all hosts
- create conflicting speaker identity systems
- allow orchestration state to live only in local SQLite
