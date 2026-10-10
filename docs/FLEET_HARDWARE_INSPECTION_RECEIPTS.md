# Fleet Hardware Inspection Receipts — opt-in, post-hoc, non-authoritative

**Draft-only research implementation (2026-10-10)**

This is an extension stacked on the **read-only AssistX fleet-hardware operator preview** (PR #269). It introduces a narrowly scoped, explicitly enabled write that adds a separately labeled **post-hoc inspection receipt to an already existing TraceGroup**. Its purpose is to make later operator hardware checks auditable without pretending those checks occurred at the original dispatch or model-execution time.

**This is not a fleet dispatcher or resource admission integration.** No source hardware snapshots, disk identifiers, internal IPs, tokens or private filesystem paths are copied into the public repository or into receipt properties.

## Why a separate relationship, not TraceEvent?

The existing `swarm_core.record_trace_event()` recomputes `TraceGroup.current_state` from the latest event type. Appending a hardware observation there would risk changing a completed/failed/running trace into an unrelated state. To avoid this, the new operation writes **only**:

```cypher
MATCH (g:TraceGroup {correlation_id: $correlation_id})
MERGE (r:FleetHardwareInspection {receipt_id: $receipt_id})
MERGE (g)-[:HAS_HARDWARE_INSPECTION]->(r)
```

The actual Cypher includes a 20-receipt-per-trace limit, immutable ON CREATE property assignments and a duplicate-retry exception to the limit. **It never creates a TraceGroup, TraceEvent, Task, Assignment, RouteDecision or reservations and does not set any execution state property.**

## Database prerequisite — schema gate before enabling writes

The `receipt_id` is SHA-256 of a canonical non-secret witness envelope (correlation ID, snapshot ID, evidence-source digest, requested resources, post-hoc kind). The writer uses `MERGE` with `ON CREATE SET`; to guarantee uniqueness during concurrent requests, a Neo4j administrator **must** apply and verify both constraints on an authorized staging database before enabling the feature:

```cypher
CREATE CONSTRAINT fleet_hardware_inspection_receipt_id_unique IF NOT EXISTS
FOR (r:FleetHardwareInspection)
REQUIRE r.receipt_id IS UNIQUE;

CREATE CONSTRAINT fleet_hardware_inspection_lock_id_unique IF NOT EXISTS
FOR (m:FleetHardwareInspectionLock)
REQUIRE m.correlation_id IS UNIQUE;
```

No schema migration is executed by the API or CI. The writer validates BOTH named constraints on every POST and returns HTTP 503 without writing unless each is present. A dedicated lock node receives a monotonically increasing fence_counter and acquires a transaction write lock before counting existing receipts. This serializes concurrent distinct receipt attempts and protects the quota of 20 without changing execution-state properties. Until an operator verifies both constraints on isolated staging, **do not enable receipt writes**. Further production acceptance must include two concurrent writers racing to record the same receipt, a deliberately dropped acknowledgment and a repeat attempt, followed by a check that exactly one node/relation exists.

## API contract

Both endpoints use the same existing AssistX Basic authentication dependency as the hardware preview. Review identity scope and reverse-proxy access; this contract is **not** a new independent RBAC system.

| Endpoint | Behavior |
|---|---|
| `GET /api/fleet/trace-inspections/{correlation_id}` | Read at most 20 previously attached inspection receipts; no current source query and no database writes |
| `POST /api/fleet/trace-inspections/{correlation_id}` | **Disabled by default**. When enabled, explicitly re-read current private evidence and attach one idempotent receipt to an existing trace only |

The explicit POST also requires an exact `X-AssistX-Operator-Intent: fleet-hardware-inspection-v1` header and body `confirm_post_hoc_observation: true`. The source root still requires the separately configured `ASSISTX_FLEET_HARDWARE_ROOT`. Additional write gating: `ASSISTX_FLEET_HARDWARE_INSPECTION_WRITES=1` (unset by default). A second mandatory gate is `ASSISTX_FLEET_HARDWARE_INSPECTION_WRITERS`: an exact comma-separated allowlist of at most eight authenticated operator principal names. **Unset, wildcard, malformed, duplicate, or mismatched identities fail closed before any private inventory or database read.** Example (staging only): `ASSISTX_FLEET_HARDWARE_INSPECTION_WRITERS=synthetic-operator`, alongside an explicitly enabled write flag. Read-only GET remains governed by normal AssistX authentication and does not require this write allowlist. This is identity scoping, not a new cryptographic authorization token or a complete RBAC replacement. Where AssistX uses a trusted authentication header, the reverse proxy must strip client-supplied copies and authenticate the header source; never accept an internet-originated actor header. The body contains only bounded resource constraints:

```json
{"ram_gib":20.0,"gpu_vram_gib":16.0,"data_host":null,"confirm_post_hoc_observation":true}
```

There is no client-supplied snapshot or digest to forge. The server computes those from the private source **at the time of inspection**, refuses stale, contradictory or unauthorized evidence, and stores only the bounded, sanitized witness. The write response includes `historical_decision_proof=false`, `admission_allowed=false`, and `dispatch_allowed=false`.

Responses distinguish a missing/malformed request (422), missing operator intent (403), feature disabled or private evidence unavailable (503), and nonexistent TraceGroup or exhausted distinct receipt quota (409). No network or filesystem workload collection is triggered by requests.

## Receipt fields and audit semantics

Receipt node properties: deterministic `receipt_id`, `correlation_id`, `snapshot`, `evidence_digest_sha256`, bounded `requirements_json`, `kind=POST_HOC_OPERATOR_INSPECTION`, authenticated actor identifier, number of preview nodes, truncation status, `recorded_at` and `recorded_at_ms` (server clock), and three explicit `false` markers for admission, dispatch and historical-decision proof.

It is **not** a signature or proof of retained source custody. Users can compare the digest to private inventory source bytes, but the hash itself does not authenticate a user or authorize work. Timestamps reflect **receipt creation**, not task execution; the trace viewer separately labels post-hoc inspections and original event-embedded hardware references.

Repeated requests for the same correlation/snapshot/digest/requirements map to one idempotent `receipt_id`. At most 20 **distinct** receipts are accepted per trace; retries remain valid at that limit. Real concurrent uniqueness needs the database constraint above.

## Staging acceptance and production hold

- Staging must use an isolated authorized Neo4j database and existing protected operator auth. Do **not** change the default environment flag or add a public/Tailscale Funnel route.
- Confirm read-only GET with no receipts returns `[]`; POST fails 503 when writes are disabled; POST requires auth, intent header and confirmation body; missing trace fails with no new group.
- Confirm a successful synthetic operator inspection is linked exactly once; conflicting or stale source evidence cannot be written; no Task/TraceEvent or `current_state` changes.
- Confirm duplicate concurrent POSTs converge to one receipt under unique constraint and lost-ack replay returns the original; test snapshot transition and limit semantics.
- Inspect audit account identity, HTTP security headers and reverse-proxy rate limits before allowing any production writer.
- Never interpret these inspection receipts as dispatch-time or execution-time evidence.

**Status:** local fake-Neo4j and FastAPI tests pass. A physically isolated disposable Neo4j 5.23 experiment passed on October 10 with synthetic data; the disposable container was removed. No production Neo4j writes, production schema installation, flag activation, merge or deployment occurred.

## Disposable physical concurrency acceptance — 2026-10-10

The repeatable test lives at tests/physical/fleet_receipt_disposable_neo4j.py. It refuses to run without an explicit disposable-lab flag, a nondefault dynamic localhost Bolt port, an empty graph, and no pre-existing constraints. It does not contain credentials or private fleet data.

A resource-limited disposable Docker Neo4j 5.23.0 container (0.75 CPU core, 1.5 GiB maximum RAM/swap) was used on x1-370, with no NAS mounts. The separate existing Neo4j container remained healthy. Only the disposable graph received schema changes; the test container was subsequently stopped and removed.

| Test | Result |
|---|---|
| Both uniqueness constraints installed on new empty graph | PASS |
| Initial receipt links only to an existing completed trace | PASS |
| 40 distinct competing receipts across six workers | Exactly 20 receipts; zero errors |
| 12 duplicate retries at full quota | Same receipt ID; still exactly 20 |
| TraceGroup execution state | Still completed |
| New TraceEvent nodes | Zero |
| Dedicated receipt lock nodes | One |
| Unknown trace correlation | No group/receipt created |
| Missing lock constraint after dropping it | Fail-closed; no write |
| Cleanup and isolation | Disposable removed; existing Neo4j retained |

Run only with explicit approval for a new disposable Neo4j container on a non-default random localhost port, after checking host load. The test requires ASSISTX_RECEIPT_DISPOSABLE_LAB=I_ACCEPT_DISPOSABLE_ONLY and NEO4J_RECEIPT_LAB_URI=bolt://127.0.0.1:PORT. It validates that the database is empty before applying its disposable-only constraints. Never use a production Bolt port or established graph.

This is bounded physical acceptance of the data-write contract, not proof that production authentication, endpoint reverse proxies, source custody, live workload admission, or runtime compatibility are approved.

Physical harness safety update: before connecting, the script independently inspects an exact-name running disposable Docker container and requires a neo4j:5.x image, a dynamic 127.0.0.1 Bolt mapping that matches the supplied URI, CPU and RAM cgroup caps, and no host bind mounts. It refuses any other target before issuing DDL or graph writes.
