# Separate model endpoint graph keys from ordinary service endpoint keys

**November 8, 2026. Forward-only source fix. Production schema and data HOLD.**

## Verified live blocker — metadata-only audit, no writes

Production `assistx-api` and Neo4j are running and healthy, on protected
source `8e8605cfb8b863be5189b702cc13e12ace4b4434`. The read-only
`SHOW CONSTRAINTS`, `SHOW INDEXES`, and aggregate `MATCH` checks found:

- Source catalog expects **53 uniqueness constraints + 86 range indexes**.
  Read-only actual catalog has **53 eligible installed uniqueness pairs**,
  **139 eligible online range index pairs**. The **one missing required
  pair** is `ServiceEndpoint.endpoint_id`; there is one additional installed
  constraint pair not in the source catalog. All 86 required indexes were
  present online. Counts alone therefore must NOT be used as readiness.
- Exactly **one duplicated `ServiceEndpoint.endpoint_id` key**, affecting
  **two nodes** out of six `ServiceEndpoint` nodes. One is an ordinary
  `ServiceEndpoint`, the other is both `ModelEndpoint` and `ServiceEndpoint`.
  Each has an `EXPOSES` relationship; the model endpoint additionally has a
  `SERVES` relationship to a `Model`. They are different semantic identities,
  **not interchangeable duplicate rows**. A deletion/merge would risk losing
  model discovery or physical service topology.
- A strict runtime read-only schema gate as proposed in draft PR #554
  would correctly refuse API cold start against this current live schema.
  **Do not deploy that gate or restart the API before authorized repair and
  separate schema bootstrap.** The live graph was not changed, and no DDL,
  service restarts, credentials or approvals were attempted.

## Root cause and source-only forward fix

`upsert_swarm_node` registers ordinary `ServiceEndpoint` by the public
`endpoint_id`. `upsert_model_endpoint` separately `MERGE`s a
`ModelEndpoint:ServiceEndpoint` by its `model_endpoint_id`, then previously
assigned the **same raw external ID** to `ServiceEndpoint.endpoint_id` via
`coalesce`. If a node exposes a service and an LLM with the same external ID,
this independently creates two valid nodes that block the ServiceEndpoint
uniqueness constraint.

In `src/assistx/swarm_core.py`, newly created `ModelEndpoint:ServiceEndpoint`
instances now receive the **opaque, versioned graph-only** key
`model_ep:v1:<sha256(external model_endpoint_id)>`. The external
`ModelEndpoint.model_endpoint_id` is **not changed**, and the `EXPOSES` and
`SERVES` graph edges still use their usual node identities. Untrusted payload
`endpoint_id` is removed from the `SET e += $props` map to prevent overwriting
the scoped key. The assignment is under `ON CREATE`; existing model endpoint
nodes keep their old key under `coalesce` until a separate approved migration.

For disjoint namespace ownership, `upsert_swarm_node` rejects ordinary
service IDs beginning `model_ep:` **before opening any write session**,
including when the invalid entry occurs later in a batch with capabilities.
The opaque key hides potentially sensitive URL-derived IDs from the
`ServiceEndpoint.endpoint_id` field; it is *not* an authentication secret.

**Do not interpret this as a completed live migration:** the existing two
colliding nodes remain unchanged. A source-only merge prepares future
creation with safer identities, but production schema readiness remains HOLD
until an authorized migration resolves the historical collision.

## Independent disposable graph gate

`tests/test_swarm_service_endpoint_key_namespace.py` checks stable hashing,
reserved-prefix denial before graph writes, distinct service/model keys for
one raw external ID, payload injection resistance, preserving the external
model ID, existing-identity non-rewrite and model relationship arguments.

`scripts/ci_swarm_endpoint_namespace_disposable.py` runs only with
`CI_DISPOSABLE_ENDPOINT_NAMESPACE=synthetic-only`. The separate GitHub
workflow creates **one disposable Neo4j Enterprise container** on a hosted
runner, listens only on runner-localhost port 27889, with synthetic auth and
**no host volumes**. It extracts the **actual Cypher strings from the exact
source AST**, then proves that under real Neo4j uniqueness constraints:

1. Ordinary service and model endpoints sharing one *external* ID can both
   be created, owner-linked and retain the model's `SERVES` edge.
2. Replaying the model's upsert is idempotent and does not replace its key.
3. Simulating an old colliding key in **the disposable graph only** makes
   real uniqueness constraint creation fail with a native schema error.
4. An explicit synthetic operator-style reassignment of the model's graph
   key restores the uniqueness constraint while keeping owner/model edges.

None of that synthetic DDL/repair is run against the real fleet database.
The main repository CI and exact-head checks remain separate release gates.

## Production migration is explicitly NOT authorized

Before touching the live graph, the accountable independent database owner
must approve a maintenance window, obtain and verify a tested out-of-band
backup, freeze competing swarm endpoint writers, record the exact existing
schema/principal/Compose fingerprint, and preserve an actionable restore
point. Under an independently held migration-only Neo4j principal, preflight
**every** `ServiceEndpoint.endpoint_id` collision, confirm exactly one
ordinary node and one model node per collision, assert the `ModelEndpoint`
identity and `EXPOSES`/`SERVES` relationships, derive each candidate new
scoped key and **assert no target key exists anywhere**. Reject ambiguous
ownership, key reuse, new writers, unexpected labels or stale source versions.

Only then may the operator transactionally update the explicitly identified
model node's graph-only `endpoint_id` and, in a separate verified migration,
create the missing uniqueness constraint. Do not `DETACH DELETE` either node,
change `model_endpoint_id`, drop unrelated constraints, or grant the running
API full admin privileges. Validate all required schema catalog obligations,
all model/owner references, independent runtime-role permissions, data and
restart behavior on a restored clone first. If the constraint/create or
post-migration checks fail, stop admission and use the verified backup/
rollback procedure rather than improvising in-place fixes. The source-only
code and synthetic evidence do **not** authorize the data migration.

Blocking issues: [auto-assist #318], security draft [#554] and fleet release
[#284]. WorkSupply remains zero slots; no Task grant, deployment or new owner
approval is produced by this patch.
