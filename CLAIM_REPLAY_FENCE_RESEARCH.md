# AssistX claim replay and capability fence — research-only patch

## Scope and evidence
November 16, 2026. Isolated source based on Auto-Assist draft PR #482,
commit `07c95e123dd2f4bd0dac69bfea13ca8dd6df0a4e`.
This changes only the `Neo4jClient.claim_task()` source-level behavior
and adds synthetic tests. It does not modify a live Neo4j database,
credentials, active Task, lease, projection or worker service.

## Reproduced backend defects (red tests before source fix)
1. The idempotent-claim branch previously matched only `Task.id`
   and `Task.claim_id` and immediately returned `claimed=true`.
   It did not verify that the requesting node still owned the claim,
   that the session matched, the Task was still CLAIMED, the lease was
   unexpired, the target still matched or the capabilities remained valid.
   The cross-owner negative test failed on the original source.
2. A fresh READY-claim Cypher predicate used
   `size($capabilities)=0 OR all(required ...)`, accepting an empty
   advertised capability list for tasks that actually required `llm`
   or `gpu`. The corresponding negative test failed on original source.

## Bounded fix
- An idempotent replay is read-only in the graph and requires current
  CLAIMED status, exact claimant, agent session, unexpired graph lease,
  proper target, all required capabilities and no Paperclip Dispatch
  relationship. A second independent Python receipt check rejects
  missing/stale/malformed Task fields; only then is `idempotent=true`
  returned. Never treat an old claim ID as persistent authority.
- When an existing READY Task still carries the same stale claim ID,
  the fresh CAS predicate rejects reuse of that ID. A new key can
  follow normal claim policy; this code does not grant permission.
- The fresh CAS now requires all actual Task capabilities even when
  caller capabilities are an empty list. The denial reason agrees.
- No new authority, API route, node token, anonymous access or
  live Task mutation is introduced. A valid claim replay is **not**
  equivalent to an approved signed physical capacity projection.

## Offline acceptance
```sh
python3 -B -m pytest -q -p no:cacheprovider \
  tests/test_fleet_idempotent_claim_replay_fence.py \
  tests/test_fleet_claim_response_shape.py \
  tests/test_executor_claims.py \
  tests/test_fleet_node_poll_contract.py \
  tests/test_fleet_node_recovery.py \
  tests/test_fleet_node_shell_gate.py \
  tests/test_fleet_node_kv_cache.py \
  tests/test_node_identity.py
```
**75 tests passed**, with one external library deprecation warning,
AST parse and git diff --check clean. All Neo4j sessions are fakes;
no real graph query or mutation, model session or credential access.

## Release boundary
**DRAFT / NO MERGE / NO DEPLOY.** Independent Neo4j/claim owner must
review the changed idempotent replay semantics (notably RUNNING/DONE
and expired claims now denied); check CAS query plan and performance
on isolated synthetic Neo4j, exact source/container CI, and verify
claim/lease/API callers. Per-node machine identity, signed projection,
fencing of heartbeat and completion, and one-node staging in issue
#501 are separate prerequisites. No credential or production changes.
