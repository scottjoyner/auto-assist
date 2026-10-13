# B02 — Harness evolution read-only projection research (October 13, 2026)

**State: DRAFT RESEARCH / production HOLD.** Stacked on PR #301's B02 consumer census.
No change to FastAPI routes, graph ingress, Docker topology, worker credentials,
query admission, user traffic, NAS, Neo4j production data or release authority.

## Scope
The exact existing `/api/harness/evolution` view calls
`Neo4jClient.get_tasks_by_status(status, limit=40)` for each of six statuses.
Its pure `harness_evolution_snapshot` aggregation uses a subset of Task fields.
This patch introduces a **non-mounted** fixed plan:
`harness_evolution_tasks_read_v1`.

- Query: `MATCH (t:Task {status:$status})` with explicit nine-field Task
  projection and `ORDER BY t.created_at_ts DESC LIMIT $limit`.
- Allowed statuses: READY, CLAIMED, RUNNING, DONE, FAILED, ERROR.
- Per-status limit: exactly 40; reject caller-supplied alternative limits/status.
- Returned fields: id, kind, title, status, target_agent_id, payload_json,
  updated_at_ts, summary, last_error; no raw-Cypher or property selector input.
- Reviewed Neo4j role needs **MATCH on created_at_ts for sorting** in addition
  to the nine returned fields; no mutation, schema or broad-property rights.
- `payload_json` is still a full payload field; it may carry secrets or user
  data. The fixture does NOT prove privacy/tenant-safe production projection.
- Fields omitted from some original Task nodes are mapped to null; deny missing
  keys in received broker maps instead of silently altering rendering.

## Focused offline tests
`tests/test_trace_b02_harness_projection_candidate.py` uses the source-pinned
`src/assistx/harness_views.py` aggregation to compare representative full
Task rows against the projection. Checks include title/failure fields,
fail_set entries, status+limit rejection, invalid types, payload upper bound,
40-row limit, missing returned keys, and denial of extra returned properties.

**Prior to PR creation:** 15/15 tests passed on x1-370 against the local
stacked B06 checkout. The exact PR-head CI result is a separate gate.

## Manual physical experiment (x1-370)
Source-compatible standalone manual test executed against cached
`neo4j:5.26-enterprise`, with an exclusive internal Docker bridge and
a synthetic ephemeral graph; no host-published graph port or production credentials.
The graph role is limited to ACCESS and MATCH on reviewed Task fields,
including created_at_ts for ordering.

Physical result **PASS, exit code 0, 20.79 seconds**, October 13:
- Restricted role authenticated; 40 READY rows; descending legacy order.
- Nine selected fields equal the legacy full-row field values.
- An ERROR Task with missing optional properties retained null projection.
- Existing harness aggregation returned identical synthetic output.
- Server-side CREATE, SET and DELETE denied for the restricted principal.
- A synthetic secret_token outside the role's property list was inaccessible.
- Ephemeral graph and network removed; Docker-managed anonymous volumes
  removed with `docker rm -fv`.

This is one physical *read-only plan and Neo4j RBAC* proof, not an accepted
authenticated production broker, network isolation, exact-head CI, real-data
compatibility, external instance attestation, throughput SLO, or B06 signed
physical closure. The committed manual runner uses existing research helper
`probe_trace_isolated_task_broker.docker` instead of the local standalone
wrapper; repeat a fresh **exact-head** manual run before physical promotion.

Manual opt-in (never run as CI default):
```sh
ASSISTX_B02_DISPOSABLE_BOLT=1 python3 tests/probe_trace_b02_harness_bolt_physical.py
```

## Next acceptance
1. Run exact-head offline CI and the exact manual fixture on an approved host.
2. Review nullable properties, payload_json PII, status and ordering semantics.
3. Authenticated staging shadow compare on existing real Task shapes and error
   paths, with read-only identity and bounded tracing. Requires approval.
4. Review all 343 B02 source indications before any B01 graph-network cutover.
5. Keep B06 independent graph identity, closure custody, and stale-effect
   fencing separate and HOLD. No automatic release or failover.

## October 13 follow-up: authorization and tenant-negative gate

Fleet Commander read-only production configuration inspection confirmed that
`TRUSTED_AUTH_HEADER`, `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` are all
**configured** in the running `assistx-api` container. Their values were
not read. The current reviewed `src/assistx/api.py::_auth_user_from_credentials`
checks the configured trusted header before the Basic credentials; the
harness evolution route depends on this generic `auth` and has no
separate operator allowlist or tenant authorization predicate.
This is a **source-level ingress trust requirement**, not proof of a live
identity spoof or credential bypass. The API host port was observed
bound to 127.0.0.1; proxy strip/forward rules were not fully verified.

New offline-only `tests/trace_b02_harness_shadow_policy.py`:
- Reuses `assistx.trace_preview_access.basic_preview_permitted`, which
  requires independently verified HTTP Basic credentials, configured
  principal and an explicit operator allowlist. Header-only authorization
  is refused. Disabled/unreviewed operator-global scope denies by default.
- `minimize_payload` strips unconsumed `payload_json` keys and unexpected
  nested chain/failure keys while preserving the existing harness view's
  synthetic output. It rejects malformed/oversized payloads, invalid types
  and credential-like endpoint URL components. It neither parses live
  credentials nor calls the graph.
- A synthetic test deliberately demonstrates that allowed `fail_set.actual`
  strings can still contain sensitive content; Task `title`,
  `summary`, and `last_error` similarly require separate data-class
  review. **No PII clearance or tenant boundary is claimed.**
- No FastAPI route is mounted. Neither a Basic operator credential nor
  a research policy fixture grants real-data or production read permission.

The **physical disposable Bolt probe** now includes a deliberate
cross-tenant **negative control**: after proving the 40-row sorted projection,
restricted MATCH grants and CREATE/SET/DELETE denials, it inserts a synthetic
Task with `tenant_id='synthetic-other-tenant'` and status READY. The
current status-only query returns that record to the same restricted reader.
The probe must observe this cross-scope result before it can pass, and
reports `tenant_isolation_proven=false` and
`global_operator_scope_reviewed=false`.
This does NOT establish actual production tenant policy or a live
cross-tenant exposure; it demonstrates a missing query-layer tenant guard.

**Local precommit verification:** 41/41 projection + shadow-policy tests
pass; updated physical probe PASS, exit 0 (20.81 seconds), with
`status_only_synthetic_cross_scope_negative_reproduced=true`.
Re-run at exact new PR SHA and reconcile CI before recording this as the
new evidence head.

**Next critical requirement:** Decide whether this endpoint is limited
to a globally privileged operator or must use a separately authenticated
tenant/task scope; document where that authority comes from and prove
the non-authorized scope is denied *by Neo4j query and broker API*, not
only in a client formatter. No live shadow/cutover is authorized until
payload content, principal provenance and tenant policy are independently
accepted.

## Optional tenant-keyed research plan (still not authorizing a live migration)

The B02 offline candidate now exposes a distinct fixed-plan identifier:
`harness_evolution_tasks_scoped_read_v1`, with a required
`tenant_id: $tenant_id` property predicate and exactly the same
nine-field projection, six status allowlist and limit=40. Invalid tenant
strings, booleans, empty identities and invalid statuses are denied by the
parameter constructor. The signature names the input
`trusted_tenant_from_server` deliberately; **the function itself cannot
prove its caller acquired a trustworthy tenant scope**.

The updated disposable Neo4j 5.26 fixture granted the synthetic reader
MATCH on the tenant_id property for filtering, seeded 45 Task rows tagged
`synthetic-primary-tenant`, and inserted a Task tagged
`synthetic-other-tenant`. Two physical assertions now pass:

1. The original status-only query returns the other synthetic tenant's
   Task (**deliberately unsafe negative reproduced**).
2. The tenant-scoped query returns 40 primary-tenant rows without the
   other tenant, and separately returns only the other row when explicitly
   parameterized for that tenant (**fixed-predicate capability PASS**).

The updated physical experiment completed in 20.82 seconds, exit 0,
with CREATE/SET/DELETE still denied, no production graph access and no
host-published ports. The new focused suite passed 54/54 local tests.

**Not proven:** existing production Task records contain a reliable tenant
property; a server-side authoritative principal-to-tenant mapping exists;
the broker prevents callers from selecting arbitrary tenant IDs;
operator-global access has been reviewed; sensitive values inside title,
summary, error messages or fail_set have been classified. Both
`tenant_isolation_proven` and `production_cutover_authorized` remain false.

This prototype is an alternative to a specifically approved global-operator
view, NOT an automatically selectable production mode. Release HOLD.

## October 13: offline broker request binding and Task-origin schema audit

New `trace_b02_tenant_broker_boundary.py` is a **pure, not-mounted**
research broker-request contract. It allows only an explicit fixed plan
and status in the caller request: any supplied tenant_id, principal,
unreviewed filter, query, or limit is denied, *including* a matching
tenant ID. It uses the existing Basic-verification/allowlist helper
to authenticate a synthetic operator principal, then resolves that
principal to a tenant solely from a separately injected, fixture
server-owned directory. No caller-provided tenant hint is used.

The research directory is **not signed, independently provisioned,
rollback-protected, or authenticated to the real broker**. The
`ReadIntent` always reports production execution and graph release
authorization false. This is a test of function behavior, not a
trustworthy deployed identity service or multi-user authentication.

### Exact source compatibility gap

Inspection of `src/assistx/neo4j_client.py` shows:
- `upsert_ticket()` populates `title`, `ticket_type`, `status`,
  `kind`, `required_capabilities`, `target_agent_id`, `priority`,
  `payload_json`, `preemptible`, and `max_migrations`, but **no
  `tenant_id`**.
- `create_tasks_batch()` likewise constructs Task property maps
  without `tenant_id`.
- The Task constraints/index list has indexes for status, kind,
  ticket_type, claimed_by, created_at_ts, and idempotency_key,
  but no declared `tenant_id` index/constraint.
- The current `/api/harness/evolution` path uses `auth` and
  `get_tasks_by_status(status,limit=40)`; it performs no
  principal-to-tenant mapping.

These are **source-only findings**, not a live data-count or a proof
that every historic Task lacks a tenant property: other producers and
manual writes can exist. Do not infer tenant from a title, payload, agent,
IP address, or current creator without an accepted owner registry. A
tenant predicate could silently omit legacy Tasks if the field is absent.

### Physical broker-facade negative and positive

The opt-in disposable Bolt test now:
1. Seeds 45 synthetic Task records under tenant A and one under B.
2. Reproduces that the status-only query returns tenant B to the reader.
3. Exercises a synthetic authenticated caller A against the fixture
   server-side A mapping; obtains only A data.
4. Repeats for synthetic caller B and obtains B data.
5. Submits a caller tenant override for B while logged in as A,
   and verifies denial **before query execution**.

The synthetic graph DB principal itself still has MATCH access to both
tenants, which means a client bypassing the hypothetical broker could
query both; B01 exclusive graph network access remains unproven.
Neither this test nor the directory provides a real signed
principal/tenant binding. A single configured Basic account cannot
substitute for a multi-user issuer.

**Local acceptance:** 84/84 focused tests pass, disposable physical
Neo4j test PASS in 23.59 seconds, exit 0. Exact remote head CI and
committed physical runner are separate gates. No real data or
production network touched.

**Remaining decision:** Explicitly authorize global-operator visibility
*or* establish per-Task tenant provenance (including legacy Task
handling), a separately governed principal-to-tenant directory and a
trusted broker ingress. Until then: NO production shadow, cutover,
reservation release or automatic failover.
