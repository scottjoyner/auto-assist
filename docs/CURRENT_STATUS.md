# AssistX current status

This page describes the repository at the current mainline checkpoint. The
dated [`STATUS.md`](STATUS.md) remains available as the earlier Paperclip
cutover record.

## Operational capability

AssistX now has a durable graph-backed control plane for:

- fleet inventory, real loaded-model visibility, and node health;
- opportunity-cost-aware task allocation and atomic reservations;
- claim leases, heartbeats, completion fencing, and stale-claim recovery;
- controller leadership with durable checkpoints and fencing tokens;
- signed typed recovery, verification, rollback, maintenance, and quarantine;
- checkpointed preemption and bounded task migration;
- bounded repository improvement with isolated Git worktrees;
- signed executor evidence, central acceptance, skill profiles, and
  review-first repair proposals;
- exact-fingerprint operator promotion with safe rollback;
- authenticated monitoring and controls in `/control-room`;
- opaque prompt-prefix KV-cache manifests, strict model/quant/runtime
  compatibility, affinity-aware allocation, TTL/eviction, and reuse telemetry.

Legacy UI paths, including `/operations`, redirect to `/control-room` and are
retained only for compatibility.

The real-Neo4j recovery canary exercises the lifecycle across recovery,
reservation ownership, checkpoint migration, improvement evidence, learning,
repair proposals, and promotion serialization.

## Authority and safety posture

- Neo4j is authoritative for task, lease, reservation, controller, recovery,
  migration, attempt, and audit state.
- A claim ID fences worker mutations. A superseded worker cannot heartbeat,
  checkpoint, or complete a newer execution attempt.
- Generic command payloads remain disabled unless
  `FLEET_UNSAFE_SHELL_TASKS_ENABLED=true`; production should leave this false.
- Recovery executes only signed typed runbooks through explicit service and
  Compose allowlists.
- Improvement workers operate only on configured repositories, allowed paths,
  allowlisted verification executables, and tier-specific file/diff budgets.
- Model output is not trusted as evidence. The executor measures Git state,
  runs verification without a shell, and signs the resulting envelope.
- An accepted improvement is still not deployed. An authenticated operator
  must approve the proposal and later promote the exact signed fingerprint.
- Promotion cannot commit, push, or open a pull request. It leaves a verified
  patch in the configured checkout for the normal release workflow.

See [`EXECUTION_AUTHORITY.md`](EXECUTION_AUTHORITY.md) for the full matrix.

## Deployment state

Recovery and self-improvement are opt-in. A node is not code-capable merely
because the API exposes the endpoints. Repository roots, writable isolated
worktree storage, node-specific signing identity, and control-plane
verification keys must be configured and mounted into the relevant process.

`GET /api/fleet/operations-readiness` reports recovery and optional
self-improvement prerequisites without returning secret values. Readiness is a
configuration gate, not proof that a live recovery or code-change canary has
passed.

The isolated [`end-to-end deployment`](end-to-end-deployment.md) bundle now
packages production-profile health, fenced task completion, cache telemetry,
cross-node migration, targeted bounded improvement, signed recovery health
checks, evidence capture, and state-preserving rollback into explicit stages.

Paperclip remains a supported execution backend. Direct Hermes execution is
also implemented. A deployment must select its intended execution authority
and must not run two consumers against the same eligible task population
without explicit reservation and idempotency controls.

## Kipnerter Agent Auto acceptance state

The authenticated Kipnerter mobile gateway and server-side Hermes credential
mapping are implemented on the current PR #50 candidate, but repository code
presence is not proof that x1-370 is running that exact source revision.

The authoritative acceptance contract is
[`KIPNERTER_AGENT_AUTO_ACCEPTANCE.md`](KIPNERTER_AGENT_AUTO_ACCEPTANCE.md).
It deliberately separates:

1. exact-head repository validation;
2. exact-source API rebuild/recreate on x1-370; and
3. one bounded live Tailnet Agent Auto request that returns HTTP 200 with
   `X-Kipnerter-Agent-Executor: hermes`.

The live verifier must preserve the existing route-scoped Tailscale Serve
configuration, loopback-only raw AssistX listener, and server-side credential
boundary. It must not use the Auto-Router admin token, add credentials to the
phone, perform direct router probes, or broaden the runtime-catalog surface.

The operational result vocabulary is `NOT_RUN`, `BLOCKED`, `FAIL`, or `PASS`.
Only a live `PASS` for the exact accepted SHA, paired with an explicitly
accepted repository-validation classification, permits the statement
**Agent Auto live path verified**. The live verifier requires
`REPO_VALIDATION_RESULT=PASS` or
`PASS_WITH_NAMED_BASELINE_EXCEPTIONS`; the latter also requires the exact
exception names. Named mainline/baseline CI exceptions are not converted into
passing tests and must be shown separately from the live result.

Every live attempt produces a sanitized machine-readable validation report and
a knowledge-base markdown report. The latter is published under the existing
Kipnerter knowledge project and summarized in the existing RC2 gateway execution
log with `scripts/publish-kipnerter-agent-auto-report.sh`. Report publication is
documentation only; it cannot promote `BLOCKED` or `FAIL` to `PASS`.

## Remaining gaps

The repository contracts are substantially implemented. The highest-priority
remaining work is cross-repository and operational:

1. execute the first signed physical observation, exact-loadout qualification,
   transactional canary, negative drills, rollback verification, and real
   non-admitted profile import;
2. define and implement the AssistX-owned profile-to-admission candidate and
   expiring/revocable admission-lease lifecycle;
3. make the router consume only current AssistX runtime projections and require
   a reservation or signed route authorization where durable assignment applies;
4. add one pinned cross-repository compatibility workflow covering LMS evidence,
   profile import, AssistX admission/projection, and router rejection behavior;
5. physically rehearse Neo4j restore, degraded activation, journal replay,
   worker promotion, leadership relinquishment, and rollback;
6. export production telemetry and define retention, cleanup, calibration,
   cohort rollout, error budgets, key rotation, and evidence revocation;
7. deploy and benchmark runtime-specific llama.cpp slot and SGLang HiCache
   adapters while keeping unsupported runtimes affinity-only.

The canonical prioritized review and acceptance criteria are in
[`SYSTEM_GAP_REVIEW_20260804.md`](SYSTEM_GAP_REVIEW_20260804.md).

These gaps should strengthen observation, integration, calibration, and
recoverability. They must not grant agents approval, promotion, release, or
unrestricted shell authority.
