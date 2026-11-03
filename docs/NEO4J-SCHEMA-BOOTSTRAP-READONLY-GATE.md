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


### Additional index metadata check

A second bounded `SHOW INDEXES` metadata query found **no standalone or constraint-owned index** for the missing `ServiceEndpoint.endpoint_id` key. This does not prove duplicate records exist; it means an arbitrary duplicate-key query may require a large graph scan. **No duplicate-data scan was performed.**

Before adding the missing uniqueness constraint, the graph owner should identify which services write ServiceEndpoint records, assess the size/cost of a bounded data-quality audit, confirm data ownership and rollback, and separately review conflicting duplicates if found. Do not perform an unconditional live `MATCH/GROUP BY` scan or `CREATE CONSTRAINT` from this read-only PR.

## ServiceEndpoint identity-collision gate — production-safe discovery

[P0 #322](https://github.com/scottjoyner/auto-assist/issues/322) isolates a **writer identity-model conflict** preventing the missing `ServiceEndpoint.endpoint_id` uniqueness constraint. The companion `scripts/neo4j_service_endpoint_collision_readonly.py` uses a single aggregate-only `MATCH` query with the **existing configured application database and read-only Neo4j session**; it returns counts, never identifiers, node properties, relationships, or graph records. The script has no merge/delete/schema operations, and an affirmative result is not release authorization.

For an authorized operator inside an existing AssistX container with its current `NEO4J_* ` environment:

```sh
python3 scripts/neo4j_service_endpoint_collision_readonly.py
```

Exit **2** means a collision or incomplete/untrusted evidence. Exit **0** means only that *the scoped ServiceEndpoint identity scan* found no current duplicate nonnull `endpoint_id` values; it is not a guarantee against concurrent new writes, app semantics or schema correctness.

### October 11 physical evidence

A value-free aggregate query found **6 ServiceEndpoint nodes**, **one duplicate identifier group**, **two involved nodes**, and **one excess node**. The two colliding nodes are structurally different: one is `ServiceEndpoint`, the other `ModelEndpoint:ServiceEndpoint`. Their property counts are 9 and 17; relationships are `EXPOSES` versus `EXPOSES` plus `SERVES`. Both are exposed by **the same SwarmNode**, but have different `service_type` values. The dual-labeled model node has matching `model_endpoint_id` and `endpoint_id`. No value of any of those identifiers or application records was printed or committed.

Deployed `swarm_core.py` has two incompatible upsert identities:

- Service registration around line 508 uses `MERGE (e:ServiceEndpoint {endpoint_id:$endpoint_id})`.
- Model registration around line 699 uses `MERGE (e:ModelEndpoint:ServiceEndpoint {model_endpoint_id:$endpoint_id})` and then sets `e.endpoint_id` to that model endpoint ID when unset.

Both can create separate nodes with the *same* ServiceEndpoint key. That explanation is supported by a fully disposable Neo4j **5.26 Enterprise** reproduction on an **internal-only Docker bridge**, with no published ports or production mounts. Only synthetic identifiers and temporary credentials were used, and its container and network were removed afterward.

| Disposable experiment | Outcome |
|---|---|
| Service registration, then model registration | **Two colliding ServiceEndpoint nodes** |
| Model registration, then service registration | **One shared dual-labeled node** |
| Repeating service MERGE after the collision | **Matches both nodes (fan-out)** |
| Creating uniqueness constraint with collision present | **Neo4j rejects conflicting data** |
| Removing only conflicting synthetic nodes, then creating constraint | **Succeeds** |
| Service registration followed by model registration after uniqueness enforced | **Model registration rejected as conflicting identity** |

This demonstrates why **neither adding the constraint nor deleting a duplicate is a complete safe repair**. The model and service writers must adopt an explicitly reviewed, order-independent canonical identity rule before any schema migration. The `EXPOSES` and `SERVES` relationship graph and model discovery clients require preservation and regression proof.

### Owner decision and release hold

No production graph DDL, merge, label removal, deletion, account migration, credential revocation, service restart, model-routing edit or firewall change is authorized by these tools. The owner must approve the identity namespace or subtype contract, migration plan with relationship retention, synthetic concurrent-write/replay tests, and rollback. **Existing production records remain untouched.**

The private x1 owner release preflight now independently requires both the **schema metadata gate** and **ServiceEndpoint collision gate** to pass, alongside four authenticated app reads, full approved caller inventory, credential custody, dual-stack Tailnet denial and unchanged-container evidence. Both schema and collision gates currently return **HOLD**.
