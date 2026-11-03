# Scoped machine claims: isolated transaction proof (NOT a production endpoint)

## Current physical and graph evidence — November 3, 2026

The live AssistX canonical projection is expired, `WorkSupplyState` has
`allow_background=false`, `total_slots=0`; the production Auto-Router is
`degraded` with zero enabled free hosted API providers. Five node agents have
matching process and protected file credentials but still receive HTTP 401.
The single existing READY Task has no explicit target, machine-capability,
or authorization fields. The **legacy** `Neo4jClient.claim_task` permits
`(target_agent_id IS NULL OR target_agent_id=$agent_id)` and an empty
capability array. `heartbeat_task` and `complete_task` also retain broad
`execution_attempt=0` and optional claim-ID predicates. Thus merely
accepting a machine token on these existing endpoints is **NOT SAFE**.

## Implemented, disabled source-only transaction

The new, unregistered Python function
`assistx.scoped_machine_claim.scoped_claim_task()` is **not exposed through
FastAPI or the fleet worker** and requires an independent
`ASSISTX_ENABLE_SCOPED_NODE_CLAIMS=1` feature switch, authenticated exact
node identity, and a trusted server policy with claim-only audience,
`["llm"]` capability, token epoch, projection generation, and model artifact
fingerprint. No credential material is copied, logged or returned.

The transactional Neo4j claim contract uses a two-statement **single write
transaction**. The first statement obtains a graph write lock by incrementing
`WorkSupplyState.scoped_claim_serial`, after checking a fresh, matching
positive background supply. The second statement, while that capacity lock
is held, requires ALL of:

- current approved canonical projection with matching generation, approval
  evidence, manifest checksum and future expiry
- same-generation fresh work supply and free/total capacity
- explicit `MachinePrincipalAdmission` approval for the **exact node**,
  artifact and token epoch, with valid approved-by/id and unexpired authority
- admitted live RuntimeInstance and LoadedModelInstance on that node with
  **explicit** `machine_chat_eligible=true`, matching artifact fingerprint;
  independently approved unexpired AccessPath and CapacityObservation
- **explicit** READY Task target, `["llm"]` capabilities, false
  `requires_approval` / `paperclip_dispatched`, true
  `machine_authorized`, matching machine-authorization generation and
  approval reference, nonempty owner identity
- no prior claim, claimed-by, session, lease or execution attempt, and no
  external Paperclip dispatched relationship
- active Task count below the approved global capacity limit

Only then does it set an exact generated claim ID, claimed-by, session,
execution attempt 1, generation, token epoch and bounded lease. It returns a
**minimal claim receipt without any Task payload**. A rejected Task raises
`ScopedClaimDenied` *inside* the transaction, rolling back the serial-lock
update, so a failed claim leaves no writes. A separate future API, if ever
authorized, must verify the node token and pass trusted policy, and cannot
bypass or call the generic `claim_task()` path.

## Actual isolated Neo4j validation

`tests/test_scoped_machine_claim.py` covers disabled/default deny, bad node
identity, malformed sessions/claims, invalid lease, stale generations,
forged/old token epochs, broader tool capability and absent approval policy.

`tests/test_scoped_machine_claim_neo4j.py` uses the repo's disposable
`neo4j_container` test fixture, **not the live `neo4j` container**. It tests
real query syntax and transactions: duplicate claim fails, second claim
contending for one slot fails, two concurrent claims with two slots succeed,
rejected attempts leave zero serial-lock writes, and token epoch, target,
Task permission/capability, projection expiry, model role, artifact,
access-path approval and capacity denies all preserve original Task state.
The container is automatically removed after tests. No secrets, real Tasks,
production Neo4j graph, leases or infrastructure were modified.

## Explicit production NO-GO

This is **a tested building block**, not completed machine-claim release:

1. The live graph has no reviewed MachinePrincipalAdmission or Task
   `machine_authorized`/model `machine_chat_eligible` evidence, and the signed
   canonical projection has expired. These are **missing approvals**, not
   records to synthesize, backfill or approve with testing code.
2. Global capacity serialization currently covers only this new scoped
   path. The legacy Task claim path, Hermes, node agents, controller,
   exception/backfill writers and signed work admission must be jointly
   inventoried, fenced and tested. Competing legacy claim writes may still
   oversubscribe capacity, so no production use can be authorized.
3. Task heartbeat, completion, failure, retry, and external side effects
   still need matching **same-transaction** claim ID, node owner, session,
   generation, token epoch, unexpired lease, revocation, actual model/runtime
   identity and independent receipt-acceptance checks (P0 #428).
4. An independently controlled release must close router inherited admin GET
   exposure; rotate or register scoped machine principals without copying
   operator Basic credentials; and run a one-node supervised staged cutover
   with full client census and rollback.

Keep **all production slots at zero** until authority owners approve actual
runtime witnesses and end-to-end claim/lease/result integrity. Never count
this prototype or simulated claims as real accepted inference work.
