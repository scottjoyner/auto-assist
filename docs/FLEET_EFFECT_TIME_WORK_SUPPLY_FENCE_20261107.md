# Fleet executor: deny stale supply at claim, model effect and commit

**November 7, 2026 — source-only security hardening, production Task admission HOLD.**

## Defect and impact

The continuous `FleetExecutor` previously derived `parallel_slots` from a
cached, projection-derived in-process inventory and subsequently could call
`Neo4jClient.claim_task`, the router and `Neo4jClient.complete_task` without
independently rechecking the **canonical graph's active grant and WorkSupply
heartbeat at effect time**. A previously good snapshot could therefore remain
in use while a controller became stale or canonical projection/approved owner
provenance changed. The durable executor leader and a `claim_id` protect other
races, but neither is a physical model-capacity lease.

## Implemented fail-closed read boundary

`src/assistx/fleet_supply_effect_gate.py` performs **one read-only Neo4j
query**, with no fallback to synthetic live capacity or an in-memory cached
WorkSupply calculation. It requires *all* of the following simultaneously:

- A valid in-memory projection generation, positive capacity and unexpired
  projection response;
- The **actual** canonical `FleetProjectionState` to be `status='approved'`,
  with exactly the same generation and a still-future expiry;
- The canonical graph state to contain a well-formed 64-hex
  `owner_approval_receipt_sha256` set by the hardened, owner-signed
  `approve-runtime-projection.py` publisher merged in #575. A digest is
  **an audit marker, not independent cryptographic proof at effect time**;
  the verified signature and owner trust are established only by the writer;
- The canonical `WorkSupplyState` to have a fresh heartbeat, generation
  matching the **same** approved projection, and `total_slots` equal to its
  deduplicated physical runtime capacity. Invalid types (including
  boolean-as-int), future-dated or expired heartbeats deny. The reader
  rejects ambiguous duplicate canonical states using strict single-result
  validation and caps freshness at **120 seconds**, even if an environment
  override requests a longer window. No auto-discovery,
  resident-model catalog or side-band SSH probe can create a Task slot.

The WorkSupply `allow_background` flag is a *background queue production*
control, not a general Task authority: `false` may mean a valid priority Task
is waiting or the backlog target is full. This check **does not misinterpret
it as global admission**; canonical owner + runtime/WorkSupply agreement is
required for all Task priorities. It does not expand a worker's privileges.

`src/assistx/fleet_executor.py` now checks the live control-plane gate before
listing/claiming READY Tasks, immediately before each individual `claim_task`
(in case the state changes during list traversal), directly before invoking
a model, during a running model's heartbeat interval (cancels in-flight
inference when authority disappears), and before completing a Task. A
revoked or expired grant leaves a Task uncommitted for existing claim/lease
expiry handling. Absent or unreachable graph, expired lease, stale WorkSupply,
missing owner grant, capacity mismatch or race **never falls back to routing**.

This is an additional **necessary control-plane check**, not a distributed
slot reservation/physical quota fence. It cannot substitute for a
resource-owned compare-and-swap lease with a bounded per-model concurrency
budget, credential-custodied production inference gateway, independently
signed approval of current resources, or an effect-time router-side fence.
It should not be represented as proof the fleet can execute productive Tasks.

## Physical read-only witness

The actual production graph was inspected without modifying it. Canonical
generation **896** has `status='approved'` but **expired**; the WorkSupply
controller remains live (around 15-second heartbeat), reporting
`projection_generation=0`, `total_slots=0`, `free_slots=0`, and
`allow_background=false`. This is exactly what a fail-closed dispatcher should
reject. The production API and Neo4j were both healthy; source changes were
made in an isolated worktree and **no production container/credential/port,
Neo4j grant or auto-renewal timer was modified**.

## Validation and remaining release dependencies

Offline tests include missing graph, invalid heartbeat clock, old
signed generation, signature digest absent/malformed, capacity inflation,
boolean-as-integer, stale/future WorkSupply, denial before any Task claim,
revocation *after claim* but before model effect, loss *during execution*
causing cancellation, and loss *before completion* preventing COMMIT. Existing
valid approved foreground/background priority and capacity tests remain green.

- `tests/test_fleet_supply_effect_gate.py` tests the **read-only Neo4j query**
  and all negative cases with synthetic data.
- `tests/test_safe_fleet_executor.py` tests real executor control flow with a
  synthetic graph and fake model runner, **no actual model or Task dispatch**.
- GitHub runs the full repository's `unit`, `recovery-canary` and handoff
  checks, plus a dedicated **test-only, contents-read** workflow for the
  affected source modules. That workflow does not connect to the fleet or
  deploy the changed executor.

The source must not be installed in the protected production checkout without
an independent release review and rollback plan. Even after installation,
the expected **current** Task admission status is HOLD until a fresh valid
owner-signed canonical approval, controlled resource ownership/lease and
credential-custodied inference transport are available. Do not reactivate
or reuse the old October keepalive authority. See #284, #569 and #501.
