# Four-service native Neo4j authority gate — API, worker, Hermes, PhoneLog

**DRAFT / READ ONLY / PRODUCTION NO-GO.** This is a research extension of
Auto-Assist draft PR #530. It neither changes the three-service gate nor
creates/rotates Neo4j users, changes grants, starts/stops containers, accesses
Task payloads, or authorizes dispatch.

## Physical evidence on November 3, 2026

A bounded read-only inspection on x1-370 independently examined the
already-running PhoneLog Python/py2neo connection using precisely these
read-only commands: SHOW CURRENT USER YIELD roles RETURN roles; and
SHOW USER PRIVILEGES.

The output retained *only grant and role counts*, not identities, raw privilege
rows, credentials, graph paths or URIs. The direct PhoneLog query found two
roles, including admin; 19 granted privileges, of which 18 are wildcard graph
grants and two are WRITE action grants. This matches the prior native findings
for AssistX API, AssistX worker and Hermes adapter.

The new fixed image-pinned four-service executable ran successfully against
the running immutable images for all four services. **Exact physical return:**
service_count=4, broad_admin_service_count=4,
broad_write_service_count=4, wildcard_graph_service_count=4,
production_decision=HOLD, dispatch_authorized=false; **process exit 3**
as intended.

This establishes effective broad administrative privileges on four running
services. It does not prove any unauthorized mutation, credential compromise,
network reachability from other principals or full writer census.

## Source trust boundary

- Fixed, single target phonelog, immutable image identity and exact running
  container ID are checked before and after the PhoneLog native readback.
- PhoneLog uses its installed py2neo package, not an absent neo4j driver;
  the two fixed SHOW commands select the system database and do not mutate it.
- The three existing AssistX processes continue using the prior #530 proven
  scanner, preserving its fixed allowlist and native privilege validation.
- The composed readback validates all four expected image pins, fixed source
  envelope and privilege types. No arbitrary shell, Cypher or service selector.
- No actual container credentials, usernames or full native responses are
  published. On missing evidence, the gate denies and prints a fixed error.
- Every result retains production_decision=HOLD and exits nonzero, **even
  if all four services become reader-only**; independent signed custody,
  physical writer census and schema bootstrap separation remain required.

## Release prerequisites

1. Expand actual direct-Bolt inventory beyond these four processes, including
   any graph-sync process, recovery tool, ad hoc systemd job and independent
   library-bound caller. Do not assume a role count establishes all writers.
2. Obtain source-owner/credential-owner approved least-privilege roles and
   independent signoff; separate Neo4j schema bootstrap from app identities.
3. Prove API, worker, Hermes and PhoneLog workflows in disposable graph
   containers under proposed deny-by-default roles, with failed write controls,
   expected allowed writes, and no broad fallback identity.
4. Establish exact deployed image/source parity and scoped graph ingress denial,
   complete claim/Task writer fencing, rollback and external custody.
5. Keep current signed Task capacity 0/0 and background execution disabled
   until physically attested signed authority and approved staging exist.

No production service, grant, user, credential, Task or database was changed.

## Tests

New source-only test module checks four fixed pins and ordering, spoofed
receipts, wrong/missing image, partial target, verified target restoration,
forged GO, role-count bounds, redaction, drift and read-only-only behavior.
These are fake Docker/graph tests; separate physical readback was done using
the actual running containers, with image pinning. Hosted CI will check source
and mock tests, **not** contact the production fleet.
