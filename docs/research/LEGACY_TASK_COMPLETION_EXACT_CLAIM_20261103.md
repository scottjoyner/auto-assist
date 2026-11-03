# Legacy Task completion exact-claim research gate

**Decision: HOLD / production NO-GO. The strict mode is OPT-IN and OFF by
default. Neither the code nor green CI closes production completion authority.**

## Baseline flaws reproduced on disposable Neo4j

On November 3, 2026, two synthetic graph reproductions succeeded with the
unmodified existing Neo4jClient.complete_task:

1. An ordinary legacy completion call changed a Task marked machine-authorized
   to DONE, without a scoped accepted-result or independent authority witness.
2. A prior completion idempotency key was reused by a different agent with
   an incorrect claim ID to change a terminal Task from FAILED to DONE.

These are source-behavior reproductions only, not evidence of a live incident.

## Proposed strict-mode contract

Set ASSISTX_ENABLE_STRICT_LEGACY_COMPLETION=1 in an isolated research process
to enter a new Neo4j write-transaction completion path. Without that explicit
setting, the exact previous implementation runs unchanged for legacy worker
compatibility. The previous default path remains unsafe; DO NOT ship or enable
production on the strength of this research patch alone.

The strict transaction:
- Requires non-empty Task, agent, opaque claim ID, and matching current agent
  session whenever the Task records a session.
- Acquires exactly one canonical WorkSupplyState row write-lock first, and
  then reads Task ownership/authority in a distinct post-lock Cypher query.
- Rejects protected machine authorization, generation, approval/epoch,
  targeted Tasks, sticky Hermes scope and Paperclip-dispatched Tasks.
- Rejects zero-attempt, claimless, wrong-owner, wrong-session and expired
  leases, even when the caller supplies a valid Basic-auth session.
- Writes Task terminal status, AgentRun and Task-linked MemoryItem inside
  the SAME transaction only for an active, still-eligible legacy claim.
- Rejects conflicting terminal rewrites. Exact idempotent retries must match
  same agent, claim, status, summary and result payload; they return the
  already-stored answer READ-ONLY, with the lock-counter write rolled back.
- Does not issue signed receipts or regard an LLM HTTP response as
  independently accepted execution.

IMPORTANT: strict mode intentionally excludes older migrated/targeted Task
completion. Initially applying it without an opt-in gate caused the real
recovery lifecycle canary to fail. The compatibility implementation keeps
that existing flow operational while permitting independent research
validation. An owner-approved migration and explicit recovery contract are
required before strict mode may be the default.

## Validation (Fleet Commander on x1-370, November 3, 2026)

- Two original source defects reproduced, independently, against disposable
  synthetic Neo4j data before the code change.
- 20/20 new strict-guard synthetic tests PASS, covering post-lock ordering,
  unique coordination row, missing claim, lease expiry and mode opt-in.
- 78/78 real disposable graph tests PASS across strict completion, scoped
  claim/execution, maintenance, Intent cancellation and existing recovery
  lifecycle (strict mode is disabled for the compatibility canary).
- 197/197 broader relevant local source tests PASS, one preexisting
  Starlette/httpx deprecation warning.
- Full assembled FastAPI operator/machine auth-isolation test PASS in a
  read-only-source container with --network none.
- Ruff, Python compile and git diff --check PASS.

Neo4j test containers were unique, resource-limited, local-Bolt only and
contained synthetic records. No live Task or production credential was used.

## Unresolved P0 release gates

1. The **default unfenced legacy completion path still exists**. Disabling
   strict mode does not protect machine Tasks, terminal idempotency history,
   or wrong claim replays.
2. Strict mode has not been migrated to existing workers, Hermes, recovery or
   Fleet Executor. Strict cutover breaks some legitimate existing target and
   claimless Task workflows. Do not switch without explicit owner approval,
   staged migration and verified canary.
3. Generic Task claim/heartbeat, update_task_status, passive claims, non-
   participating direct Bolt clients and all revocation, reaper or outside
   resource effects are not exhaustively fenced. The source writer census
   still marks dozens of dynamic/callback sites unresolved.
4. No independent signed result custody, anti-rollback high-water,
   machine-principal replay prevention, physical source-writer privileges or
   source-authoritative production lease exists.
5. Five legacy node agents still have Task listing HTTP 401; production
   signed capacity is expired and slots remain 0.

Production NO-GO. No production deployment, credentials, running Task graph,
model, route or signed approval were changed.
