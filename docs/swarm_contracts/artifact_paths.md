# Artifact Path Contract

_Last updated: 2026-05-26_

## Purpose

This contract defines how binary files, generated outputs, transcripts, clips, model artifacts, and reports are referenced across the swarm.

The filesystem/NAS is the authority for binary payloads. Neo4j is the authority for metadata, provenance, relationships, retention policy, and task linkage.

---

## Core rule

Do not assume one absolute path works from every node.

Every artifact should be represented by a logical storage root plus one or more resolved path forms.

---

## Artifact schema

```yaml
artifact_id: string
kind: audio | video | image | transcript | detection_csv | clip | report | model | manifest | other
storage_root: string
relative_path: string
host_path: optional string
container_path: optional string
tailscale_host_hint: optional string
uri: optional string
sha256: optional string
size_bytes: optional int
created_at: ISO-8601
updated_at: ISO-8601
producer_repo: auto-assist | Sophia | auto-ingest
producer_task_id: optional string
source_event_id: optional string
retention_class: ephemeral | keep | protected | evidence
privacy_class: private | sensitive | public | unknown
```

---

## Storage roots

Recommended initial roots:

```yaml
- name: nas1
  description: Primary NAS mount on x1-370 generation
  example_host_path: /media/scott/NAS1
  example_container_path: /nas

- name: s_drive
  description: S-drive / audio-first canonical storage
  example_host_path: /mnt/S
  example_container_path: /ssd-ingest

- name: deathstar_legacy
  description: Legacy deathstar-XPS-8920 source paths
  example_host_path: /mnt/8TB_2025/fileserver

- name: local_ssd
  description: Node-local fast scratch storage

- name: model_cache
  description: Local model weights/cache directories
```

---

## Retention classes

| Class | Meaning | Cleanup Behavior |
|---|---|---|
| `ephemeral` | Temporary or recomputable | may be pruned |
| `keep` | Useful durable artifact | do not prune without policy |
| `protected` | Important user/history artifact | never prune automatically |
| `evidence` | Legal/claim/evidence-grade | never prune; require explicit handling |

---

## Path mapping example

```yaml
artifact_id: art_20260526_001
kind: transcript
storage_root: nas1
relative_path: fileserver/dashcam/transcriptions/2026/05/26/example.json
host_path: \/media\/scott\/NAS1/dashcam/transcriptions/2026/05/26/example.json
container_path: /nas/fileserver/dashcam/transcriptions/2026/05/26/example.json
tailscale_host_hint: x1-370
sha256: optional
retention_class: keep
privacy_class: private
```

---

## Neo4j node model

```cypher
(:ArtifactRef {
  artifact_id,
  kind,
  storage_root,
  relative_path,
  host_path,
  container_path,
  tailscale_host_hint,
  sha256,
  size_bytes,
  retention_class,
  privacy_class,
  created_at,
  updated_at
})
```

Relationships:

```cypher
(:Task)-[:PRODUCED]->(:ArtifactRef)
(:SignalEvent)-[:REFERENCES]->(:ArtifactRef)
(:Transcription)-[:DERIVED_FROM]->(:ArtifactRef)
(:MemoryFact)-[:SUPPORTED_BY]->(:ArtifactRef)
(:VoiceTrainingSample)-[:USES_CLIP]->(:ArtifactRef)
```

---

## Hashing policy

- Hash small metadata/transcript files by default.
- Hash large video/audio opportunistically or in background.
- Store `sha256` when available but do not block ingest on hashing multi-GB files unless the artifact is evidence/protected.

---

## Implementation checklist

- [ ] Add `ArtifactRef` schema helper.
- [ ] Add path resolver for host/container/Tailscale contexts.
- [ ] Add storage root registry.
- [ ] Add retention class enforcement.
- [ ] Add background hash job for large protected/evidence artifacts.
- [ ] Update auto-ingest outputs to emit `ArtifactRef` payloads.
- [ ] Update Sophia training clips to emit `ArtifactRef` payloads.
