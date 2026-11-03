# Neo4j ServiceEndpoint identity migration — synthetic two-node rehearsal

**Draft research, NO-GO for production migration, credentials and Graph/SGNC release.**

This opt-in lab extension stacks on the exact PR #324 disconnected
canonical writer candidate. It does not import a production API, copy
production graph records, open production Bolt endpoints, or deploy a new
client. All data is generated inside a Neo4j 5.26 Enterprise container
on an **internal-only network**, with **zero published ports and no
production mounts**. The base harness forcibly removes the container
and network after the test, even on failure.

## Known live blocker — not part of the lab data

The separately collected November 3, 2026 read-only production
aggregate shows six ServiceEndpoint nodes, one duplicate endpoint_id
group (two nodes, one excess), and the required
ServiceEndpoint(endpoint_id) uniqueness constraint still absent.
Current AssistX API, worker, Hermes, and PhoneLog identities also have
admin/wildcard/WRITE grants. No live endpoint IDs, properties, edge
destinations or credentials are present in this research commit.

## Reproducible opt-in scope

1. Run the inherited PR #324 11-scenario canonical writer test first,
   including model-first/service-first, idempotency, foreign owners and
   synchronized conflicting/concurrent registration.
2. In **the same disposable graph only**, temporarily drop the newly
   created synthetic canonical uniqueness constraint. Seed **two
   artificial nodes** bearing one synthetic endpoint ID, both exposed by
   one synthetic owner, with a model subtype serving one synthetic model.
3. Verify that adding the uniqueness constraint while both nodes exist
   **fails**, and leaves all synthetic nodes and relationships intact.
4. Run a pure in-memory structural preflight: it must identify
   EXACTLY one service-only node and one service/model subtype, one
   shared owner, approved service type, known labels/relationship
   types, one SERVES model, and no unexpected properties or edges.
   Reject foreign owners, extra labels/edges, missing records, unknown
   fields and divergent model identities **without graph mutation**.
5. **On this known test fixture only**, explicitly choose the model
   subtype as the surviving canonical node. A single test transaction
   preserves the existing EXPOSES owner and SERVES model semantics,
   copies one safe synthetic port field, deletes the other synthetic
   node with *plain DELETE* (not DETACH DELETE), and verifies counts.
   The transaction aborts if any unexpected residual edge exists.
6. Add the canonical uniqueness constraint; prove it is present;
   use the still-UNWIRED PR #324 writer to re-register the reconciled
   synthetic identity idempotently. **No production release**.
7. Verify that source-guard/offline tests, the 19 physical booleans,
   no-ports/no-mounts/internal-network evidence and cleanup all pass.

Command from the exact detached checkout:

    PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q tests.test_endpoint_collision_rehearsal_guard tests.test_endpoint_identity_candidate tests.test_endpoint_identity_candidate_disposable_guard

By default the physical harness exits with **HOLD / code 2** and
executes no Docker commands. Only the explicit disposable test runs:

    python3 scripts/endpoint_identity_collision_rehearsal_disposable.py --run-disposable

## Precise limitations and independent acceptance obligations

**This is NOT a live migration algorithm**. It only accepts a single,
newly generated, perfectly understood synthetic pair. The real nodes
have distinct property maps, different original identity predicates,
possibly additional inbound/outbound relationships, downstream
referents, service discovery consumers, and provenance. A count-only
production diagnostic cannot authorize survivor selection, property
mapping, edge rewiring, deletion, or loss of node identity.

The real migration requires:

- Authorized graph owner approves a redacted, individually reviewed
  relationship/provenance and consumer dependency map for both records
  without exporting sensitive graph content into public CI.
- An isolated copy reproduces the *actual schema/edge shapes*, including
  foreign owners, unknown relationship types and rollback; unknown
  semantics must **HOLD** rather than choose a survivor.
- Prove a controlled dual-writer quiescence boundary before migration,
  then the exact canonical writer and schema constraint, consumer model
  inventory, event retry, idempotency and concurrent ownership semantics.
- Independently validate scoped read/write runtime principals for
  AssistX API, worker, Hermes and PhoneLog, separate privileged schema
  bootstrap, cross-database permission denial and cold-start rollback.
- Physically test direct Bolt/graph writer isolation and external
  custody issue #51 before any SGNC release decision.

All observed tests are performed under fleet-admin control, not an
independent custodian. Keep Auto-Assist #318/#322, SGNC #51 OPEN, the
research PR DRAFT/UNMERGED, and **production NO-GO**.
