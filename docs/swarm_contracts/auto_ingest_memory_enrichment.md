# auto-ingest Memory Enrichment Contract

_Last updated: 2026-05-26_

## Purpose

auto-ingest is the historical-memory enrichment system for Scott's offline swarm.

It is not the real-time AssistX control path. It periodically enriches the unified `neo4j` memory graph with new historical context from local media, transcripts, metadata, and derived summaries.

---

## Cadence

Expected cadence:

```text
weekly/monthly/on-demand
```

Not expected:

```text
daily mandatory ingest
real-time blocking dependency for Sophia voice commands
```

---

## Responsibilities

- scan new historical sources
- normalize transcripts and metadata
- preserve source provenance
- classify memory value
- filter music/noise/media segments
- extract opinions/preferences/entities
- generate summaries and embeddings
- produce batch review summaries
- update unified `neo4j` memory graph

---

## Batch lifecycle

```text
planned -> scanning -> normalizing -> classifying -> extracting -> reviewing -> promoted -> archived
```

## Batch node schema

```yaml
batch_id: string
started_at: ISO-8601
completed_at: optional ISO-8601
status: planned | scanning | normalizing | classifying | extracting | reviewing | promoted | failed | archived
source_roots: list[string]
file_count_seen: int
file_count_processed: int
transcript_count: int
candidate_memory_count: int
promoted_memory_count: int
music_or_media_count: int
low_confidence_count: int
error_count: int
summary: string
```

---

## Segment classification

Every transcript segment should receive a memory-value classification before promotion.

```text
scott_speech
passenger_speech
conversation
music_or_media
navigation_or_alert
ambient_noise
unknown_low_confidence
```

### Promotion policy

| Classification | Promote to memory? | Notes |
|---|---:|---|
| `scott_speech` | yes, if confidence high | primary memory source |
| `conversation` | maybe | review if sensitive/unclear |
| `passenger_speech` | maybe | avoid asserting as Scott opinion |
| `music_or_media` | no | keep as searchable source only |
| `navigation_or_alert` | no | not personal memory |
| `ambient_noise` | no | source only |
| `unknown_low_confidence` | no by default | review only |

---

## Memory candidate schema

```yaml
candidate_id: string
batch_id: string
source_artifact_id: string
source_segment_id: optional string
classification: string
confidence: float
candidate_type: fact | preference | opinion | event | location | relationship | note | unknown
text: string
summary: optional string
entities: list[string]
embedding_required: boolean
review_status: pending | auto_promoted | rejected | needs_review
created_at: ISO-8601
```

---

## Review output

Each batch should produce a human-readable review summary:

```yaml
batch_id: string
review_path: ArtifactRef
new_files: int
processed_files: int
candidate_memories: int
auto_promoted: int
needs_review: int
rejected_music_media: int
errors: int
recommended_next_actions: list[string]
```

---

## Neo4j model

```cypher
(:IngestBatch {batch_id, status, started_at, completed_at})
(:MemoryCandidate {candidate_id, classification, confidence, candidate_type, review_status})
(:MemoryFact {id, text, confidence, created_at})
(:ArtifactRef {artifact_id, storage_root, relative_path})
```

Relationships:

```cypher
(:IngestBatch)-[:FOUND]->(:ArtifactRef)
(:IngestBatch)-[:PRODUCED]->(:MemoryCandidate)
(:MemoryCandidate)-[:SUPPORTED_BY]->(:ArtifactRef)
(:MemoryCandidate)-[:PROMOTED_TO]->(:MemoryFact)
(:MemoryFact)-[:SUPPORTED_BY]->(:ArtifactRef)
```

---

## Embedding policy

Generate embeddings for:

- promoted memory facts
- high-confidence Scott speech segments
- summaries
- extracted opinions/preferences

Do not generate first-class memory embeddings for:

- music lyrics/media transcripts
- low-confidence hallucinated segments
- navigation prompts
- ambient noise

These may remain keyword-searchable as source records.

---

## Interface with AssistX

auto-ingest should publish batch summaries to AssistX.

AssistX should expose:

```http
POST /api/events
POST /api/ingest/batches/{batch_id}/review
```

AssistX should be able to create follow-up tasks:

- review candidate memories
- approve/reject memory promotions
- run embedding backfill
- repair failed file paths
- rescan a source root

---

## Implementation checklist

- [ ] Add batch ID to historical ingest runs.
- [ ] Add segment classification step.
- [ ] Add music/media/noise classifier.
- [ ] Add `MemoryCandidate` output.
- [ ] Add batch review report artifact.
- [ ] Add embedding backfill for promoted nodes.
- [ ] Add AssistX review task creation.
- [ ] Add tests with known song/media transcripts.
