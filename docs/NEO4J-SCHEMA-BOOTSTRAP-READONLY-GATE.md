# Neo4j schema bootstrap / least-privilege gate — read-only evidence

Issue: [P0 #318](https://github.com/scottjoyner/auto-assist/issues/318). Separate dependencies: [credential custody #156](https://github.com/scottjoyner/auto-assist/issues/156) and [Fleet Hardware ingress #44](https://github.com/scottjoyner/fleet-hardware/issues/44).

**DRAFT / NO DDL / NO CREDENTIAL ROTATION / NO CONTAINER RESTART.**

## Observed source behavior

The deployed AssistX `api.py` invokes `Neo4jClient.ensure_schema()` in the API `lifespan`. Errors are logged as warnings and startup proceeds. Consequently, a successful cold start and `RETURN 1` query cannot prove that schema bootstrap succeeded.

The deployed `Neo4jClient.ensure_schema()` has **139 static declarations**, consisting of **53 node-property uniqueness constraints** and **86 standalone property indexes**. Its statements include CREATE CONSTRAINT and CREATE INDEX; these privileges must not be silently retained on a long-lived runtime account merely to make startup succeed.

## Reproducible read-only drift witness

The portable `scripts/neo4j_schema_drift_readonly.py`:

- Parses the exact application schema literals with Python AST, without importing the application or running its startup hooks.
- Rejects unknown, nonliteral or duplicate schema declarations before connecting to Neo4j.
- Uses only `SHOW CONSTRAINTS` and `SHOW INDEXES` with the current application's **explicit** database and existing credentials in a `READ_ACCESS` session.
- Requires exact node-label/property matches and an `ONLINE` independent range index; a constraint-owned backing index is not counted as a standalone index.
- Defaults to **counts only**. `--include-missing-names` is for restricted owner use; do not paste private schema inventories into public logs.
- Returns exit **0** only when the declared schema metadata is complete. Exit **2** means drift or unverified evidence. A pass is *not* privilege-, data-, write-, or release-authority proof.

To run *inside an appropriately authorized AssistX container* after this script is present:

```sh
python3 scripts/neo4j_schema_drift_readonly.py \
  --source /app/src/assistx/neo4j_client.py
```

It requires existing `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, and explicit `NEO4J_DATABASE` in that container's environment. Never export those values to a workstation just to run the audit.

## October 11, 2026 physical evidence

Running the exact PR script through Python stdin **inside the existing AssistX API container** (without copying secrets or modifying its filesystem) yielded:

| Measure | Observed |
|---|---:|
| Static constraint declarations | 53 |
| Live matches | 52 |
| Missing declarations | **1** |
| Static standalone range indexes | 86 |
| Live ONLINE matches | **86** |
| Declared indexes unavailable or not ONLINE | 0 |
| Read-only status | **HOLD / exit 2** |

The single missing declaration is uniqueness of `ServiceEndpoint.endpoint_id`. The live database additionally has a `KgNode.id` constraint, which is *not* in the current AssistX bootstrap list; that extra object is **not automatically an error**, because other graph clients may own it.

This source-based result does **not** prove whether the missing constraint was never created, failed due to duplicate data, was intentionally removed, or was deleted inadvertently. No graph records were read or written. **Do not automatically issue the missing `CREATE CONSTRAINT` from this report.** DDL could fail on conflicting data or interfere with other graph workloads.

## Smallest safe replacement-principal experiment

1. Assign explicit owners for the two application databases, the graph schema, Tailnet policy and credential issuer. Preserve the existing production identity until a tested cutover.
2. In a disposable graph instance, test a **schema-migration principal** with database-scoped index/constraint privileges. Stage idempotent schema declarations under that principal.
3. Define distinct database-scoped AssistX and PhoneLog **runtime** roles. They require independently tested app reads/writes and only the scoped metadata-visibility privileges necessary for schema verification. They must not inherit global role/user administration.
4. Add an **explicit, owner-enabled runtime verify-only startup mode** that checks required schema and fails closed if missing, while making no DDL attempts. Preserve the old startup behavior until this migration is independently approved.
5. Validate cold API start, worker/recovery path, PhoneLog real ingestion, schema idempotency, and cross-database access denial in disposable data. Do not use a green read-only query as a replacement for write tests.
6. Separately finish credential custody, the complete permitted graph-client inventory, and reversible IPv4+IPv6 network containment before promoting any production release.

**Current boundary:** only the AST parser, schema-metadata audit and synthetic tests are implemented in this draft. The proposed verify-only startup mode, scoped roles, real ingestion test and production network containment are **not implemented or authorized**.
