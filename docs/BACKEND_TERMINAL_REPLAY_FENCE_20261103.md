# Legacy backend Task completion idempotency: read-only replay fence

**November 3, 2026 — source-only research change; production HOLD.**

## Physical fleet status

Nine Linux nodes remained reachable in Fleet Commander. Local model-serving
endpoints still answer, but the canonical AssistX `WorkSupplyState` reports
`allow_background=false` and `total_slots=0`, because canonical projection
approval expired. Auto-Router is `degraded`, zero enabled free hosted routes;
five running legacy node agents still receive HTTP401. x1 has almost no free
swap and Herder's memory admission gate correctly blocks new local model
sessions. None of these gates is changed by this patch.

## Reproduced server-side defect

`Neo4jClient.complete_task()` previously matched an old `AgentRun` using just
`Task.id` and `AgentRun.completion_id`. If an old caller reused that key with
a different terminal status, the idempotency branch unconditionally ran
`SET Task.status=$status` and rewrote result, completed-by, and timestamp.
It never checked the current Task's owner, session, claim ID, lease or
execution attempt. A stale completion from a prior run could therefore
un-FAIL or overwrite a newly claimed Task *before* the worker could reject
the HTTP acknowledgment. The frontend and worker fixes in draft PR #515 do
not repair that graph mutation.

## Narrow source correction

A replay with an existing `completion_id` now reads **at most two** related
`AgentRun` rows. Duplicate keys fail closed rather than selecting an arbitrary
run. For exactly one, the server returns an idempotent, **READ-ONLY** receipt
only if original AgentRun and the **current Task** agree on all of:

- Task identity, requested terminal status and original run terminal status;
- `agent`, `completed_by`, nonempty claim ID and agent session ID;
- original run's saved claim ID/session and current Task's same exact values;
- explicit positive integer execution attempt, same on run and Task;
- unchanged result JSON and summary.

A mismatch or missing legacy proof returns `None` **without writing to the
graph**. An old AgentRun with no stored claim/session/attempt cannot be
upgraded retroactively to an authorized completion. When a Task needs to
recover after an earlier FAILED completion, it must be separately and
legitimately re-claimed under a **new** execution attempt/key; replaying its
old completion key is never a valid way to change status.

For a **new non-idempotent** completion, the server now requires the Task to
be `CLAIMED`/`RUNNING` and owned by that agent (closing the previous first-
attempt wildcard that allowed any Task status), checks any supplied claim and
session identity, and refuses an expired lease. A new AgentRun saves the
claim ID, effective agent session, and attempt, binding future reads to the
same attempt. New Task status/result changes still need the separate
signed-generation/lease/revocation CAS planned for P0 #428.

**Important remaining legacy exception:** Some callers still omit claim IDs
and session IDs. This minimal fix does not silently break those live clients
by globally requiring both parameters on every *new* completion. The
mode-specific strict-claims wrapper and future machine-scoped API must enforce
that after a complete client-owner migration. This draft is **not** production
admission or an independently accepted model result.

## Verifiable isolated tests

`tests/test_backend_completion_replay_cas.py` uses only the repository's
`neo4j_container` fixture (ephemeral Docker Neo4j, never live Neo4j). It
confirms the full claim→complete→identical-replay lifecycle without any extra
writes; rejects changed terminal status (`FAILED`/`CANCELLED`), agent, claim,
session, summary and output JSON; denies old-key replay after Task reset,
reclaim, owner change, status change, claim change or attempt rotation; refuses
ambiguous duplicate completion keys, READY-task unclaimed completion and
expired lease. Two concurrent completion calls for one Task create exactly
one `AgentRun` and a subsequent same-key replay is read-only.

The adjacent recovery, swarm, worker and executor tests are also exercised.
No synthetic `DONE` or source-only test is counted as an independently
accepted **production** Task. Test containers are automatically removed.

## Explicit production NO-GO

- Five fleet agents still need correct least-privilege machine identities;
  do not copy operator Basic credentials into node services.
- Generic `claim_task`, `heartbeat_task`, and other completion/retry writers
  still lack one globally enforced signed generation/lease/revocation fence.
  New scoped code in PRs #504/#508/#509 is draft and not yet deployed.
- Canonical signed Task supply is expired with zero slots. Independent
  result signature, external custody and anti-rollback are not restored.
- Router admin GETs remain anonymously reachable across Tailnet.
- Herder's x1 swap-reserve hold must not be bypassed. Production Neo4j
  backups need independently verified integrity and disposable restore.

**No production graph writes, model loads, credentials, services, approvals,
router routes, swaps or leases are modified.** This change is stacked on
worker-scope [PR #515](https://github.com/scottjoyner/auto-assist/pull/515)
and must remain draft until all relevant source owners complete release gates.
