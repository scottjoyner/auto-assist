# AssistX and Auto-Router read-only runtime custody — October 17, 2026

**Source-only security tooling with an actual production read-only Docker
inspection; NOT production release authorization.**

The script scripts/read_only_runtime_provenance.py inspects a fixed allowlist
of API, worker, Hermes, Neo4j and Auto-Router container names using only
Docker container inspect. It never runs Docker exec, queries graph databases,
reads external secrets stores, restarts services, alters networks, or changes
model residency. It intentionally does not print graph usernames, connection
URIs, passwords or any other environment variable value. Graph equality
relationships use temporary G1/G2 aliases, not a persistent hash.

The source-to-image binding remains UNVERIFIED even when a container label
claims an otherwise syntactically valid Git revision. An OCI label is not a
signed SLSA or equivalent build provenance attestation.

## Fleet Commander read-only snapshot (2026-10-17 11:19 EDT)

- API, worker and Hermes all running at their previously witnessed immutable
  Docker image IDs and start timestamps.
- All three share the same Neo4j user *reference* and the same Neo4j URI
  *reference*, seen through ephemeral equality aliases. This is **not**
  an independently inspected effective Neo4j SHOW PRIVILEGES census.
- The production Neo4j container has three attached Docker networks, not
  a dedicated Context-only network. Network names and credentials are not
  embedded in this public-facing report.
- Container tags / revision labels provide **no verified** cryptographic
  source-to-image binding.
- A runnable sealed Auto-Router candidate remains separate; it is not proof
  of production source or admission authority.
- No production Docker, graph, credential, model or service mutation was
  made by the inventory. The inspection reported HOLD.

## Test evidence and release criteria

- Test suite for this read-only inventory plus supervisor/executor regression:
  47 passed, 0 failed (Fleet Commander x1-370).
- Ruff on the changed code passed.
- The test deliberately introduces synthetic secret values into fake Docker
  env/config fields and proves they are omitted from JSON output.
- Corrupt image SHA, wrong container name, missing graph reference,
  duplicated graph key and Docker read failure fail closed.
- Actual production production user identity equality remains a **blocker**.

Before production release, establish independently verified:
(1) immutable Git source -> signed image provenance -> runtime container ID,
(2) native effective graph privileges and least-privilege principal transition
for API/worker/Hermes, with a rollback plan,
(3) resource-owned effect-time, non-restorable COMMIT fencing and negative
replay/co-restore/split-brain tests,
(4) exact-head hosted CI with nonzero executed job steps, and
(5) independent operator approval of restricted promotion.

**Decision: HOLD / NO-GO for production failover or credential cutover.**
