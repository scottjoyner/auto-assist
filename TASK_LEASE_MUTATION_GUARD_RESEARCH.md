# AssistX Task heartbeat and completion lease-fence research

**Research only, November 16, 2026.** Stacked on idempotent claim
PR #587 source, independently isolated at local commit
`e8f2da3f4c258be5cb02022f26efe0e2e0367e20`.
No live Task, Neo4j DB, worker unit, credentials, or inference invocation
was used.

## Previously observable source behavior
1. `Neo4jClient.heartbeat_task` accepted a current claim ID without
   validating the lease had not expired or a provided session still matched
   before refreshing `lease_expires_at_ts`. Existing zero-attempt legacy
   behavior is separately unresolved (see limits).
2. `Neo4jClient.complete_task` had analogous omissions in its current-
   claim mutation branch (claim-id optional, no lease-expiry or provided
   session check).
3. An idempotent completion lookup matched `Task.id` and
   `AgentRun.completion_id`, returned a terminal receipt, **and could
   rewrite the Task status/result** on a replay whose requested status
   differed. Agent identity, claim and original result were not verified.

## Bound change
- For Tasks with a current claim, heartbeat and completion now require
  an exact nonempty claim fence, current lease expiry, claimed-by identity,
  and matching session *when supplied*. These are **graph predicate
  preconditions**, not after-the-fact Python checks.
- Heartbeats cannot set terminal DONE/FAILED/CANCELLED states;
  terminal Task changes must use the fenced completion route.
- Idempotent completion replay becomes **read-only** and only succeeds
  when Task and original AgentRun agree on the agent, terminal status,
  claim fence and provided session. No retroactive `FAILED` to `DONE`
  status change is allowed via reusing a completion key.
- Newly created AgentRun nodes store the current claim ID to bind future
  exact retries; older runs without that field will not be silently
  promoted to valid claimed-work replays.
- The `coalesce(execution_attempt,0)=0` branch is **not redesigned** here.
  It is a separate legacy admission flaw needing a full caller inventory,
  migration plan, and explicit stricter policy approval.
- A supplied node identity or claim ID is not independently authenticated
  by these methods. Machine principals, token/session binding, signed
  runtime capacity and API ingress are release prerequisites.

## Evidence
A synthetic stale heartbeat test **failed on the original source**.
The patched source and test suite:
```sh
python3 -B -m pytest -q -p no:cacheprovider \
  tests/test_task_mutation_lease_fence_research.py \
  tests/test_fleet_idempotent_claim_replay_fence.py \
  tests/test_fleet_claim_response_shape.py \
  tests/test_executor_claims.py \
  tests/test_fleet_node_poll_contract.py \
  tests/test_fleet_node_recovery.py \
  tests/test_fleet_node_shell_gate.py \
  tests/test_node_identity.py
```
**93 passed**, one unrelated Starlette/httpx deprecation warning.
AST parsing and diff whitespace checks pass. All graph sessions in the
new tests are synthetic. No production persistence was touched.

## Release decisions
**SOURCE TESTS PASS / HOSTED CI NOT YET VERIFIED / LIVE HOLD.**
Requires independent Task/DB ownership review of legacy zero-attempt
behavior, idempotency contract and AgentRun schema; isolated staging
Neo4j CAS+transaction tests; exact-head hosted CI; operator-owned
machine credential, signed capacity, canary and rollback approval.
Do not merge, deploy, restart, claim, complete, heartbeat or mutate
production Tasks based on this research branch.
