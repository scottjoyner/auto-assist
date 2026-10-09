# Preregistered research: trace → task → fleet registry evidence (October 8, 2026)

**Review branch:** `feature/assistx-trace-evidence-20261008`, stacked after [trace context draft #124](https://github.com/scottjoyner/auto-assist/pull/124). **No production deployment or query.**

## Verified prior design facts
- The existing trace producer stores `TraceEvent.task_id` and may create explicit `TraceEvent-[:FOR_TASK]->Task` relationships. Those properties/relationships are **producer assertions**, not signed fleet node provenance.
- `Task.worker_id` and `Task.node_id` are projections of assignment signal **payloads**, and can be stale, contradictory or invented.
- `SwarmNode.node_id` registry entries may be self-announced and do **not** prove that a particular trace was executed on that node. Its presence alone never warrants an `attested` label.
- The current UI context panel accepts **top-level trace event properties**, does not parse payloads, and intentionally declines to name a verified node/agent.

## Falsifiable predictions
1. A separate **authenticated, on-demand, read-only** endpoint will return at most **12** task evidence rows for one trace, with a 13th solely for a truncation indicator. The graph query matches exact correlation ID, **explicit FOR_TASK relationship and the same event's matching top-level task_id**. Unlinked same-named tasks must never appear.
2. Optional `Task.node_id` matching against `SwarmNode.node_id` can be labeled **registry ID corroboration**, not source or identity authentication. Zero matches means not found, exactly one means unverified registry match, multiple matches mean **ambiguous**; none are `verified`.
3. Task worker/node/status values are flagged **mutable task projection / assignment claim**, not authoritative trace ownership. A missing worker or node stays unknown, and unsafe or oversized identifiers cannot be rendered.
4. The client makes **zero new evidence calls on initial page or trace load**. An explicit operator **Inspect registered evidence** button requests a bounded read-only GET for that one trace. It renders no IPs, credentials, raw payload, SSH or execution links; the panel has no fleet actions.
5. Race fencing must discard evidence responses for a previous trace. Errors, authenticated 401, older servers and absent graph facts display unavailable/unknown, never invented positive attribution.
6. A no-event/unknown trace is HTTP 404; malformed correlation ID >128 chars is HTTP 422. Real production database performance and authenticated browser acceptance are **separate gates**.

## Acceptance and safety
Test branch with a fake Neo4j session and FastAPI dependency-injected auth, plus Node VM UI tests. Query must be bound, read-only, and 4-second bounded. Do not query the live graph for this experimental slice. No graph writes, trace data export, NAS, provider routing, model inference or background execution. Keep #121 → #122 → #124 stacked and this follow-on draft. Full historical retention #117 and source-attestation #125 remain unresolved.
