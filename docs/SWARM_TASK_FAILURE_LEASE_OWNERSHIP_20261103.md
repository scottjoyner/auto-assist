# Legacy swarm Task failure and lease mutation fences — November 3, 2026

**SOURCE ONLY / DRAFT / PRODUCTION HOLD.** This change is stacked on
[PR #520](https://github.com/scottjoyner/auto-assist/pull/520), which makes
backend Task completion idempotency immutable. It does not authorize or
activate any production LLM Task execution.

## Physical context

Fleet Commander independently checked x1-370: canonical AssistX background
Task supply has `allow_background=false` and `total_slots=0` because the
signed projection approval is expired; Auto-Router remains degraded with zero
enabled free hosted providers. Five existing node agents still have rejected
Task-poll credentials. Herder's resource gate remains held with only tens of
MiB of swap free, dominated by production Neo4j Java. No swap, Neo4j
configuration, graph or credential changes were performed.

## Reproduced legacy source defect

`swarm_core.fail_task()` previously did `MATCH Task {id}` and unconditionally
`SET Task.status`, `failure_count` and `agent_session_id`, without checking
current status, owner, session, claim or lease. A stale or foreign `agent_id`
could turn another worker's CLAIMED/RUNNING/DONE Task into READY or FAILED.
`set_task_lease()` likewise extended any Task's lease by Task ID alone, with
no ownership or expiry check. `release_expired_task_leases()` used
`coalesce(t.lease_expires_at_ts,0) < now`, treating **missing** leases as
expired, and `now_ms or _now_ms()` ignored an explicit zero timestamp.

## Source-only corrections

- **`fail_task`** accepts an active Task only with `claimed_by=agent_id`,
  exact *non-null* `claim_id` and `agent_session_id`, status CLAIMED/RUNNING,
  and a future lease at the time of the write. The swarm fail route now
  accepts and forwards `claim_id`. It refuses terminal/review Tasks and
  stale/foreign/missing credentials without any graph mutation. A valid
  retryable failure returns the Task to READY and clears old ownership,
  session, claim and lease. The existing *unclaimed* READY compatibility path
  is limited to a Task with **no** claim/session/owner and no caller claim;
  it remains an operator-controlled legacy API, not machine authorization.
- **`set_task_lease`** requires exact owner, claim and session, a currently
  CLAIMED/RUNNING Task, unexpired old lease, and a positive integer lease
  between 1 and 900 seconds. Obsolete Task-ID-only callers fail safely
  (return False) instead of extending unknown ownership or raising.
- **`release_expired_task_leases`** releases only an active Task with an
  *explicit*, expired lease. It also clears old owner, session, claim,
  claimed time and lease; `now_ms=0` now means zero instead of current time.
  Missing lease evidence does not authorize release.
- All three legacy mutation functions **refuse any Task explicitly marked
  `machine_authorized=true`**. Such Tasks require their own independent,
  signed-generation, token-epoch, claim and revocation-fenced controller. Do
  not use the legacy sweeper to reassign those Tasks.

## Real isolated tests

`tests/test_swarm_task_ownership_fences.py` exercises a disposable Neo4j
container and has cases for exact active failure, retry and reclaim, terminal
idempotency, foreign owner/session/claim, absent claim/session, missing or
expired lease, prior Task status DONE/CANCELLED, explicit machine Task
exclusion, legacy ID-only renewal refusal, two competing failure/renewal
calls, and expired sweep racing a late failure. Two API-boundary tests verify
`TaskFailIn.claim_id` reaches the actual mutation function and that a wrong
claim causes an HTTP denial without a Task write. It uses no real Task IDs or
secrets. The upstream backend completion-replay suite also includes a
repeated 10-round parallel completion race discovered under the broad suite:
each claimed Task must have **one** persisted AgentRun. The backend now takes
its Task write lock **before** finding a prior idempotency key or checking
current Task state, and uses one `session.execute_write` for the lock, Task
transition, AgentRun creation and optional outcome memory. Rejected writes
and valid read-only retries roll back the preliminary lock increment.

## Remaining HOLD: not globally serialized

This draft **does not** certify global Task authority. In particular,
`maintenance.py` zombie reapers, direct status mutation, passive-claim paths,
`execution_control` migrations/revocations, generic Neo4j claim/heartbeat,
external Task creators/cancellers, and independently approved signed work
supply **do not all coordinate with this lock**. Separately, legacy unclaimed
READY failure remains accessible to operator credentials, not scoped
machine tokens. Need a cross-writer, cross-approval transaction policy and
negative race tests covering revocation and all maintenance paths before
machine Task claims can be enabled.

Production admission, node identity, model artifact/path, independent
accepted-result custody and anti-rollback remain NO-GO. Do not merge or
deploy in isolation; do not count synthetic outcomes or event-outbox
deliveries as productive LLM generations. No live graph, model, credentials,
swap or service changes were made.
