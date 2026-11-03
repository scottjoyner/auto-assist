# Scoped machine Task observation — gated research-only route

## Motivation and confirmed physical blocker

November 3, 2026 Fleet Commander verified five active legacy node agents
(xwing, Deathstar, Destroyer, Lenovo, OptiPlex) whose live process Basic
credentials equal the secure per-node `agent.env` values but still receive
**HTTP 401** from AssistX `GET /api/agent/tasks`. That operator-Basic endpoint
and its claim/heartbeat/complete siblings are not safe to replace by a naive
`X-Fleet-Node-Token` bypass: the existing
`Neo4jClient.list_agent_tasks(agent_id=...)` includes **untargeted** READY
Tasks, and `Neo4jClient.claim_task` also permits targets with NULL agent ID.
Only two node IDs are currently registered for token identity. The canonical
runtime approval is expired, and Task work supply is zero.

## Implementation: limited opt-in *observation*, not execution

A separate `GET /api/fleet/nodes/{node_id}/assigned-task-hints` is disabled
by default (`ASSISTX_ENABLE_SCOPED_NODE_OBSERVATION` must be exactly `1`).
It does not amend the operator Basic Task endpoints or grant claims,
heartbeats, completions, access to Task payloads or any admin route.

Before querying Task metadata it requires:

1. A valid `X-Fleet-Node-Token` for the **exact** route node ID in the
   configured `ASSISTX_FLEET_NODE_TOKENS` registry. Missing, cross-node,
   unregistered or wrong tokens are denied; operator Basic is never fallback.
2. An explicit server-side per-node entry under
   `ASSISTX_SCOPED_NODE_OBSERVATION_POLICIES` with
   `enabled: true`, `audience: targeted-llm-observer`, exact
   `allowed_capabilities: ["llm"]`, positive integer
   `projection_generation`, and a 64-hex-character approved
   `artifact_fingerprint`. Absent or stale configuration denies access.
3. The existing read-only canonical projection approval evaluator, which
   requires approved state, resolved generation/revision and approver/id,
   and a future expiry. The generation must equal the node's policy.
4. Fresh `WorkSupplyState` matching this generation, positive **total and
   free** slots, and `allow_background=true`. Expired/zero/denied work
   supply is a 503 HOLD, not a fallthrough to ordinary Task queries.
5. Current admitted physical `RuntimeInstance`/`LoadedModelInstance` on
   that node, approved nonexpired `AccessPath` and `CapacityObservation`,
   exact policy-bound artifact fingerprint, and explicit model capability
   list containing `chat` or `llm`. An embedding-only model is insufficient.
6. A parameterized Cypher query with **exact**
   `target_agent_id=$node_id` and `status=READY`:
   **explicit** `requires_approval=false` and
   `paperclip_dispatched=false` (missing properties fail closed), and
   `required_capabilities=['llm']`, capped at 20 hints. Untargeted Tasks are
   categorically excluded. The **final Task query rechecks** canonical
   approval expiry, generation and fresh work-supply capacity in the same
   read query, so a mid-request revocation produces no Task IDs. The response
   includes Task IDs only, no prompt,
   title, payload, tools, transcript, owner or context. Responses have
   `Cache-Control: private, no-store` and always explicitly report
   `authorizes_claim=false`, `authorizes_dispatch=false`.

This is a **metadata observer** intended to stage a least-privilege machine
identity and prove scope before adding any scoped claim/side-effect APIs.
No Task operation is dispatched and no signed runtime projection is minted by
this endpoint. The legacy source proof checks existing approved graph state;
it does *not* replace independent physical/cryptographic witness custody or
an exact signed router admission proof.

## Synthetic configuration shape (NOT live credentials)

```json
{
  "node-illustrative": {
    "enabled": true,
    "audience": "targeted-llm-observer",
    "allowed_capabilities": ["llm"],
    "projection_generation": 17,
    "artifact_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
}
```

This is a test/example only. Do **not** enable this feature or install such
a policy on production without independent source/approver/rotation checks.

### Live schema-drift finding

Read-only Neo4j `EXPLAIN` validated all three parameterized query shapes on
November 3, 2026, without executing them or returning any Task payload.
The live graph reported **unknown-property warnings** for
`Task.requires_approval` and `Task.paperclip_dispatched`. Therefore a
`coalesce(missing,false)` predicate would have incorrectly treated legacy
missing authorization evidence as a positive permission. The final Task
hint query instead requires these properties to be **explicitly false**;
missing values cannot match. Until a separately approved Task creation
contract writes these attestations, this observer cannot return those
older Task IDs. Do not bulk-backfill or infer them from old payloads.

## Validation and remaining hard holds

Isolated TestClient+fake-Neo4j regressions cover disabled/default deny,
no operator Basic fallback, unregistered/revoked/cross-node tokens, no policy,
wrong/stale generation, missing/unpinned artifact, zero supply, missing
runtime, embedding-only and malformed model role, bounded limits, no Task
payload, no claims, and exact-target Cypher. Wider node identity, existing
read-only readiness, executor, and token-auth tests run alongside it.

**Remaining P0, unchanged:** fleet node credential custody and two-vs-five
registered identities; full source-owner census; all Task claim/heartbeat/
completion entry points must enforce transactional node/owner/session/
generation/lease/revocation/attempt fences; independently signed current
runtime witnesses and one durable, independently verified Task result. The
router's anonymous admin GETs are still reachable across the Tailnet.

This PR is **draft and production NO-GO**; no live credentials, services,
model state, Task graph, leases, signed approvals or routing were changed.
