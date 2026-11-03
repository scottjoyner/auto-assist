# Source-only Task writer census - research HOLD

Baseline anchor: local bbe81eb43873102ac9fe672865224c65635c5d8e; exact same source tree (750bf2960cac9affae7c1b925c4fe52439e36d01) published at draft PR #516 commit 9d591042b19e9b60de1d1b18ceb5fb0d3836ad40.
This is an AST lexical inventory of tracked Python, NOT exhaustive runtime writer authorization.

## Counts

| Item | Count |
|---|---:|
| Tracked implementation Python files scanned | 252 |
| Candidate .run/query/transaction sites | 410 |
| Queries with Task label and mutation keyword | 81 |
| Dynamic query sites | 32 |
| Unresolved transaction callbacks | 9 |
| Total unresolved sites | 41 |

81 is a candidate count, not 81 confirmed Task writers. Some .run uses may be unrelated to Neo4j. Older inventory numbers use different source scope and criteria; do not compare directly.

## High-priority source-owner review

| File | Function | Reason to qualify |
|---|---|---|
| src/assistx/neo4j_client.py | claim_task | Legacy shared-pool claim, NULL target/capability |
| src/assistx/neo4j_client.py | heartbeat_task | NULL claim ID, lease/session and revocation fencing |
| src/assistx/neo4j_client.py | complete_task | Stale claim/attempt-zero and terminal rewrite |
| src/assistx/maintenance.py | maintenance_job | Lease reapers / terminal retention |
| src/assistx/execution_control.py | reconcile | Preemption and migration Task status changes |
| src/assistx/intent_orchestrator.py | _handle_cancel | Cancellation versus active claim |
| src/assistx/passive_claims.py | claim_passive_task | Parallel passive claim writer |
| src/assistx/api.py | api_pause_task | Direct API Task status changes |
| src/assistx/scoped_machine_claim.py | scoped_claim_task | Research claim row-lock only |
| src/assistx/scoped_machine_execution.py | scoped_execution_transition | Research heartbeat/finalization; no active route |

## Candidate site distribution (static Task write plus unresolved)

| Source file | Task write candidates | Dynamic query | Transaction callbacks |
|---|---:|---:|---:|
| src/assistx/neo4j_client.py | 29 | 11 | 1 |
| src/assistx/api.py | 8 | 2 | 0 |
| src/assistx/swarm_core.py | 5 | 5 | 0 |
| src/assistx/passive_claims.py | 5 | 0 | 4 |
| src/scripts/summarize_from_segments.py | 5 | 2 | 0 |
| src/assistx/routers/review.py | 4 | 1 | 0 |
| src/scripts/approve_and_execute.py | 5 | 0 | 0 |
| src/assistx/execution_control.py | 4 | 0 | 0 |
| src/assistx/intent_orchestrator.py | 4 | 0 | 0 |
| src/assistx/maintenance.py | 3 | 0 | 0 |
| src/assistx/control_room.py | 0 | 2 | 0 |
| src/assistx/neo_schema.py | 0 | 2 | 0 |
| src/assistx/routers/transcriptions.py | 2 | 0 | 0 |
| src/assistx/scoped_machine_claim.py | 1 | 0 | 1 |
| src/assistx/scoped_machine_execution.py | 0 | 1 | 1 |
| src/assistx/voice_routes.py | 2 | 0 | 0 |

## Required gate before productive machine claims

1. Review all Task-write candidates and unresolved sites; map generated Cypher, indirect mutations and non-Python writers separately.
2. Build an owner and direct-Bolt privilege inventory for every Task create, cancel, claim, heartbeat, complete, revoke, reaper, retention, migration and external effect.
3. Have all eligible Task mutation writers join the same real database lock/authority transaction with post-lock read, OR block them from touching protected Tasks with physical DB privilege proof.
4. Run disposable Neo4j adversarial concurrency tests on reapers, cancellations, revocations, terminal writes and nonparticipating writer bypass.
5. Obtain independent anti-rollback external receipt custody and real resource-effect evidence; never treat graph booleans or source receipt as external confirmation.

## Reproduce without DB access

python3 scripts/audit_task_writer_sites.py --summary-only
python3 scripts/audit_task_writer_sites.py --fail-on-unresolved --summary-only (intentionally exits 2 while unresolved)
python3 -m pytest -q tests/test_task_writer_census.py

The scanner emits path/line/function/digest/category/confidence but never Cypher text. Untracked or generated source, other languages, externally controlled Bolt users and running service identities are outside this scan. It always reports HOLD and does not authorize production work.
