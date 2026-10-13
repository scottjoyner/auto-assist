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
