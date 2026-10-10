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

The `receipt_id` is SHA-256 of a canonical non-secret witness envelope (correlation ID, snapshot ID, evidence-source digest, requested resources, post-hoc kind). The writer uses `MERGE` with `ON CREATE SET`; to guarantee uniqueness during concurrent requests, a Neo4j administrator **must** apply and verify this constraint on an authorized staging database before enabling the feature:

```cypher
CREATE CONSTRAINT fleet_hardware_inspection_receipt_id_unique IF NOT EXISTS
FOR (r:FleetHardwareInspection)
REQUIRE r.receipt_id IS UNIQUE;
```

No schema migration is executed by the API, CI or this PR. The writer now inspects Neo4j schema metadata on every POST and **returns HTTP 503 without writing** unless this exact constraint is present. Until an operator verifies the constraint on isolated staging, **do not enable receipt writes**. Further production acceptance must include two concurrent writers racing to record the same receipt, a deliberately dropped acknowledgment and a repeat attempt, followed by a check that exactly one node/relation exists.

## API contract

Both endpoints use the same existing AssistX Basic authentication dependency as the hardware preview. Review identity scope and reverse-proxy access; this contract is **not** a new independent RBAC system.

| Endpoint | Behavior |
|---|---|
| `GET /api/fleet/trace-inspections/{correlation_id}` | Read at most 20 previously attached inspection receipts; no current source query and no database writes |
| `POST /api/fleet/trace-inspections/{correlation_id}` | **Disabled by default**. When enabled, explicitly re-read current private evidence and attach one idempotent receipt to an existing trace only |

The explicit POST also requires an exact `X-AssistX-Operator-Intent: fleet-hardware-inspection-v1` header and body `confirm_post_hoc_observation: true`. The source root still requires the separately configured `ASSISTX_FLEET_HARDWARE_ROOT`. Additional write gating: `ASSISTX_FLEET_HARDWARE_INSPECTION_WRITES=1` (unset by default). The body contains only bounded resource constraints:

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

**Status:** local fake-Neo4j and FastAPI tests implement these invariants; no real Neo4j write acceptance, schema installation, flag activation, merge or deployment has occurred.
