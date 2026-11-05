# P0 #318 — split Neo4j schema bootstrap from long-lived runtime credentials

**DRAFT / production HOLD.** Stacked on Auto-Assist PR #553 at exact SHA f5db63ed5fb980c9a9c62f36114e757ac2d2278d.

## Problem confirmed in source

The API lifespan invoked Neo4jClient.ensure_schema(), executing up to 53 CREATE CONSTRAINT and 86 CREATE INDEX statements under the long-lived application principal. Errors were swallowed as startup warnings. This binds normal API availability to broadly privileged Neo4j credentials and obscures incomplete schema, including physically observed missing ServiceEndpoint.endpoint_id uniqueness caused by an existing two-node collision (issue #322). This is source behavior, not new inspection of the live host.

## New source behavior

- The API lifespan uses only a READ_ACCESS Neo4j session with two SHOW queries for constraints and indexes. The schema catalog is pinned to the same 53 uniqueness constraints and 86 online range indexes declared by the existing bootstrap function. There is no runtime CREATE, CALL, SET, MERGE, schema auto-repair or admin fallback.
- A missing constraint, unavailable SHOW privilege, offline/unrecognized index, malformed catalog, or connection failure is fatal to API startup. It cannot silently continue into Task, model or recovery activity with an unverified schema. Errors expose aggregate counts, not identifiers or credentials.
- The existing ensure_schema method now requires explicit_operator_migration=True before privileged DDL. This boolean is a defensive source-intent guard, **not** independent operator approval.
- The CLI 'init' path requires '--explicit-operator-migration' and separately supplied NEO4J_SCHEMA_OPERATOR_USER / NEO4J_SCHEMA_OPERATOR_PASSWORD, refusing absent credentials or equality with the standard NEO4J_USER / NEO4J_PASSWORD. Neither values nor usernames are logged.
- The recurring CLI 'run-all' verifies schema read-only and no longer runs schema DDL before ingestion.
- A wider AST source scan identified the alternate recovery-shadow lifespan also ran `ensure_schema` and suppressed failure. It now uses the same read-only metadata verification and fails before mounting inherited runtime handlers on schema HOLD. Emergency recovery visibility independent of the main application requires a separately isolated control-plane design; this draft does not silently bypass the missing constraint.

## Required operator release gates

1. Obtain a verified root+EFI backup and out-of-band console on x1-370 before any host package maintenance; preserve existing four graph-consuming services and deny any restart while their source image/credential states are unverified.
2. Finish collision-safe endpoint identity reconciliation in a disposable graph per issue #322 / draft #536. The original live collision contains two noninterchangeable nodes with different labels/properties and EXPOSES/SERVES references. Never automatically detach, merge or relabel production data.
3. Independently prove on a disposable Neo4j Enterprise 5.26 instance that all 53 constraints and 86 online indexes can be created by a separately held schema operator, then verify the API starts under a restricted per-database runtime principal allowed only SHOW constraints/indexes and required application read/write operations. Check cross-database, DBMS, user administration and schema DDL denial.
4. Rehearse API, worker, Hermes and PhoneLog cold start, normal reads/writes and a transaction rollback with isolated data, and verify privilege scope separately for each consumer. This PR does not modify all four consumers' credentials.
5. Perform the actual rollback-safe cutover only after independent operator approvals, separate source/image custody, registry/revocation and physical off-host denial tests. This draft is not permission to change production schema, grants, passwords or host services.

The currently observed production schema is known to be **missing one constraint**; this strict new runtime would refuse to start until the collision is safely reconciled and the full schema is externally migrated. Do not merge or deploy to production now. All user production services remain unchanged. NO-GO.