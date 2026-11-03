# Fleet native graph principal separation — research HOLD / production NO-GO

## Native production findings — Fleet Commander, Nov 3, 2026

Independently image-pinned running containers were inspected using only
SHOW CURRENT USER and SHOW USER PRIVILEGES in Neo4j READ_ACCESS sessions.

| Running service | Roles | Admin | Grants | Wildcard graph grants | WRITE grants |
| --- | ---: | --- | ---: | ---: | ---: |
| assistx-api | 2 | YES | 19 | 18 | 2 |
| assistx-worker | 2 | YES | 19 | 18 | 2 |
| assistx-hermes-adapter | 2 | YES | 19 | 18 | 2 |

Each native census requires an exact immutable Docker image pin and validates
that the running container stays at the same identity both before and after
the read. Output has only a fixed service name and aggregate counts, no
usernames, passwords, URIs, query rows or untrusted headers. All three
production processes have Neo4j connection settings, and all three strict
legacy Task-writer flags are NOT ENABLED.

Current Compose source contains shared Neo4j credential references. The
API, worker and Hermes adapter use the same primary Compose network.
Neo4j has multiple container networks and published port bindings.
Neither network membership nor a Compose file alone establishes that
untrusted processes cannot reach write authority.

**Evidence:** 3/3 inspected production services have administrator and WRITE
rights. Admin capability bypasses the idea that every physical database writer
must participate in the cooperative canonical WorkSupplyState transaction
protocol. This proves an authority boundary gap, NOT a credential compromise
or an observed malicious production Task write.

Production canonical WorkSupplyState last read showed exactly one row,
0 total/0 free work slots, background disabled and an expired signed
capacity projection. The source writer census remains HOLD with 48 unresolved
query/transaction callback sites. Legacy five-node authenticated Task GET 401
and independent receipt custody remain separate release gates.

## New three-service image-pinned research census

scripts/read_only_fleet_graph_principals.py reuses the existing, previously
tested scripts/read_only_native_graph_privileges.py fixed native query runner.
It accepts ONLY three allowlisted containers and requires a separate immutable
SHA256 Docker image ID for each. It performs each existing native census
sequentially with no user-controlled Cypher, shell command or container name.
It fails closed on any missing, changed or mismatched identity and restores
the native target on error. It never prints credentials or raw privilege rows.

Manual use:
    python3 scripts/read_only_fleet_graph_principals.py \
      --expected-api-image sha256:FULL_IMAGE_ID \
      --expected-worker-image sha256:FULL_IMAGE_ID \
      --expected-hermes-image sha256:FULL_IMAGE_ID --json

The actual production command returned 3 admin principals, 3 broad-WRITE
principals and 3 with wildcard graph grants; production_decision=HOLD,
dispatch_authorized=false and exit code 3 (a deliberate failed gate).
An eventual all-reader result would STILL say HOLD rather than GO until
independent writer authority, ingress and custody are verified.

## Physical disposable role proof

An independent random-password research principal and role were created
ONLY on a uniquely named, resource-limited, loopback-Bolt Neo4j Enterprise
disposable database. The role was granted ACCESS ON DATABASE neo4j and
MATCH {*} ON GRAPH neo4j NODES Task, WorkSupplyState.

Eight local disposable database tests passed: the role read synthetic Task/WorkSupplyState
but could not see unrelated MemoryItem records; five attempted graph mutations
(SET Task, CREATE Task, DETACH DELETE, SET WorkSupplyState, MERGE Task)
all failed with Neo4j native Security errors. It also could not grant itself
WRITE on the system database. The admin fixture confirmed no unexpected Task
or coordination state changes. No production role, grant or secret changed.

The physical role-creation negative test source remains local research evidence,
not part of the published PR because its file could not be published through
the connected source transport. Hosted PR CI therefore runs the source-only
three-service census tests and the existing 171 disposable graph/recovery
regressions, NOT the eight new role-creation negative cases. Do not claim a
hosted physical RBAC proof from this PR.

READ_ACCESS driver routing is not a security grant; a restricted database
role is necessary for a defensible read-only observer.

## Required owner-approved cutover plan — NOT executed

1. Census all physical Bolt principals, effective grants, clients and
   network paths; pin runtime identities and keep secrets out of logs.
   Review API, worker, Hermes, outside tools and backup/recovery dependencies.
2. Create separate observer READ identities constrained by label and property,
   and a narrowly authorized Task mutation gateway. Do NOT simply replace
   the API/worker/Hermes credentials with a read-only principal: these
   processes perform legacy writes and would break without migration.
3. Prevent direct Bolt access by uncontrolled clients and remove broad
   WRITE/admin credentials from application services. Role-level WRITE
   alone cannot enforce a canonical lock or owner/lease/revocation condition.
   Only a controlled mutation gateway with signed evidence and post-lock
   comparisons can enforce that Task authorization protocol.
4. Complete all 48 unresolved source query/callback sites; audit external
   side effects, child/outbox replay, stale claims, session revocation,
   model inference admission, and independent graph evidence custody.
5. Validate on disposable Neo4j and in staged canary service/recovery flows:
   wrong-identity Task SET/CREATE/DELETE denial, lost DB privileges, stale
   app credentials, credentials rotation, signed zero-slot admission,
   successful rollback and no external effect duplication.
6. Seek explicit approval for real credential, secret, database grant and
   route changes only after independent acceptance, custody and runtime
   identity gates are demonstrated.

DRAFT / HOLD / production NO-GO. No running principal, database role,
credential, Task, graph node, service, model, route, signed work
admission or external dispatch was changed.
