# Mercury specialist shadow adapter — preregistration and observed results

**Date / decision purpose:** 2026-10-08, America/New_York. Evaluate Mercury Bots as scoped specialist workers for useful free-model work without granting Mercury independent provider-quota or fleet dispatch authority.
**Decision:** Implement a disabled-by-default, **fixture-only** shadow contract. Live providers, production routing, externally accessible Mercury webhooks, paid fallback and automatic retry remain forbidden. This is an engineering research branch, not a fleet policy change.
**Upstream pin:** cosmicstack-labs/mercury-agent at `1be98293ace567092b961346d95363284f6e71cf` (MIT license); observed source in `/home/scott/git/upstream-mercury-agent-20261008`.
**Governance:** knowledge `10-Infrastructure/governance/FREE-PROVIDER-LEASE-NEXT-CHECKPOINT.md` remains `CHECKPOINT_NOT_REACHED`.
**Prediction provenance:** The prior knowledge PR #51 preregistered the broad gates. The detailed assertions below were refined while implementing and running this fixture suite; they are not presented as independently preregistered before all test executions.

## Predictions registered for this mock-only slice

| ID | Predicted acceptance | Observation |
| --- | --- | --- |
| P1 | Disabled or missing-authority tasks produce zero worker events and no lease acquisition. | Mock tests pass. |
| P2 | Unknown specialist, unqualified upstream alias, paid/non-fixture model, malformed digest and foreign worker fail before execution. | Mock tests pass. |
| P3 | Lease denied, expired, wrong group, or renewal failure stop generation before advancing next simulated event; 401/402/403/429/503 trigger one synthetic circuit report with no retry. | Mock tests pass. |
| P4 | Per-role input/output/step caps prevent an over-budget mock outcome; no task can claim successful completion without a finish event and observed usage. | Mock tests pass. |
| P5 | Private, chained receipts detect subsequent tampering; a failed admission receipt prevents worker start; duplicate task IDs never redispatch. | Mock tests pass. Local mutable hashes are **not WORM**. |
| P6 | Quality and vendor quota verification remain unproven even when the mock lifecycle completes. | Explicitly false in each Outcome. |

These observations are limited to our Python stand-in, **not** an end-to-end Mercury BotManager, OpenCode, Hermes, LLM gateway, NAS witness or genuine upstream lease test.

## Implemented contract

- Source: `scripts/mercury_shadow_specialists.py` (stdlib-only, no HTTP client, no subprocess launch, no external inference).
- Tests: `tests/test_mercury_shadow_specialists.py`.
- Specialists: `repository-reviewer` (6,000 input / 800 output, 3 steps); `trace-auditor` (3,000 / 500, 3); `documentation-analyst` (5,000 / 900, 3).
- Fixed provider/model/upstream are `mercury-fixture`, `mercury-fixture/no-generation`, `synthetic-upstream-only`. Real aliases and live models are rejected, not interpreted as independent quota pools.
- No tooling, file reads, code modifications, autonomous subagent creation, model request, credential access or webhook activity occur. `FixtureWorker` is the *only* accepted worker class, and the adapter is disabled by default.
- The simulation requires initial lease admission, checks renewal **before advancing** each synthetic event, and releases its lease at terminal state.
- Journal receipts include role, task correlation ID, objective SHA-256, synthetic provider/model/group, local observation time, event sequence, previous hash, current hash, bounded token counters and terminal reason. They omit prompt bodies, file contents, credentials, tool output and model responses.
- Journals use owner-private local append+fsync and lock; corrupt histories, untrusted file modes, symlinks and prohibited fields fail closed. No cleanup or rotation is performed. The prototype journal is not independently witnessed, cannot prove NAS custody and does not promise cross-host uniqueness.

## Reproduce with no model tokens

```bash
cd /home/scott/git/wt-auto-assist-mercury-shadow-20261008
python3 -m pytest -q tests/test_mercury_shadow_specialists.py
python3 -m py_compile scripts/mercury_shadow_specialists.py
git diff --check
```

**Observed 2026-10-08:** 40 fixture-only tests passed in 0.32s; compilation and diff check returned success. No provider calls, measured token burn, vendor quota readings, Mercury daemon startup, or production task execution occurred.

## Why Mercury cannot simply receive webhooks yet

At the pinned upstream revision, `src/web/api/bots.ts` exposes `POST /api/bots/:id/hooks/:hook`; it authenticates/deduplicates and calls `botManager.enqueue`, which eventually reaches `bot-manager.ts:executeTurn`. The code does not integrate our shared **physical-upstream-provider lease** ahead of the actual model request, and webhook enqueue/202 acceptance is not proof of admitted generation. Its default fleet concurrency is eight. Bots can auto-create/delegate peers, so a naive webhook bridge multiplies pressure and bypasses the shared quota gate.

The current `BotManager` queue lease protects resumable **jobs** (60-second renewable worker-job lease), not free-provider account quotas. Mercury's local token accounting and rotating journals must not be used as authoritative cross-node budget or durable audit custody. Additionally, `src/bots/types.ts` explicitly says an empty or missing `tools.allow` means all tools except denied tools; `permissions.yaml` is the current single source for per-bot tool grants. Future real bot manifests must use a **nonempty explicit read-only allowlist** and narrowly scoped read paths, not `allow: []`, and must deny shell/write/skill installation and unrestricted peer delegation. This remains unimplemented.

## Safe next implementation slice (not activated)

1. Define a *new* pre-dispatch authority interface inside an isolated Mercury fork/wrapper, explicitly off unless provider-group identity is verified, and disable alternative webhook/run paths that could bypass it.
2. Carry a caller-authenticated `task_id`, specialist, allowed operations, exact provider/model/upstream ID, max steps/tokens/time, source digest, and immutable trace correlation ID across AssistX → router → Mercury.
3. For each emitted token/tool event, enforce live lease validity; on a renewal failure, revocation or coordinator disconnection, stop streaming and revoke tool execution; ensure no automatic requeue results in generation without reacquiring a fresh lease.
4. Send typed, redacted receipts to the existing authoritative trace store and verify independent NAS custodial hashes **before** local compacting. Never let Mercury's 5 MiB / three-rotation journal be the canonical record.
5. Prove a real task's useful quality through separately checked source citations and tests; track accepted work per million observed tokens, not just HTTP status or claimed "$0".
6. Only after explicit operator authorization, preregister a one-node live generation attempt; multi-host admission, provider physical-account sharing and error-budget checks require a separate approval and fault matrix.

## Deployment and rollback

No installed service or live config was changed. No worker runtime is exported or wired into API/router routes. Rollback is reverting this additive code/document branch. Existing provider timers, free-provider pause, AssistX deployment snapshot, NAS recovery and operational tool history remain untouched.

**Remaining blocking research:** no proven production key authority, cross-host lease coordination, independent WORM witness, real mid-inference cancellation, physical quota limits, model generation results, real quality acceptance, or Mercury UI integration. A full disk requires denying new work rather than trimming trace history.

## October 8 follow-on preregistration: transactional mock-only denial slice

**Scope and authority:** Operator approved the previously recommended mock-only
hardening of draft PR #130. No live provider access, Mercury BotManager startup,
production routing changes, real keys, second node, or hosted inference is authorized.
The fixture's sqlite database must live on trusted local storage, never SMB/CIFS.
This addendum records the expected outcomes before running the *new* focused tests;
the original 40 regression tests were already executed during development.

- **H1 — concurrent group admission:** eight synthetic contenders sharing one
  SQLite fixture will receive no more than one simultaneous upstream-group lease.
- **H2 — idempotency:** reusing a task ID after denial, crash/restart, or release
  will never re-admit; a different task is only eligible if the fixed group budget permits.
- **H3 — exact scope:** altered node, role, attempt, model, epoch, or expired/revoked
  lease is rejected before the fake provider-call counter increments.
- **H4 — quota:** a full reservation is charged before a mock dispatch. The
  fixed mock upstream-group budget will not be independently multiplied by aliases
  or refreshed by mere worker completion.
- **H5 — custody:** failed synthetic acknowledgement before start, or before
  provider-call-site evaluation, stops the corresponding simulated work.
- **H6 — ingress:** synthetic webhook, cron, message, replay, and peer calls
  are denied at the fake provider-call boundary with zero fake provider calls.

**Non-claims:** In-process acknowledgements are not independent WORM witnessing;
SQLite is not a production authenticated coordinator; simulated cancellation does
not prove Mercury SDK mid-stream revocation; the fake call site never invokes a
model or exercises the actual BotManager. Any report must separate mocked counters
from real token usage and independently accepted work.

## Follow-on observations (October 8, 2026; after preregistration)

- **69/69 offline tests PASS** (0.42 s) across the original fixture tests
  and the new transactional/mock-call-site suite; Python syntax compilation
  and whitespace checks passed. No real provider generation occurred.
- A trusted-local SQLite fixture uses an immediate transaction for task-ID
  uniqueness and a single active physical-upstream-group reservation.
  Eight concurrent synthetic contenders admitted exactly one; parallel
  same-task adapter invocations started exactly one fixture worker.
- Reservations count against finite group capacity even after mock release:
  neither completion nor an alias can manufacture restored credits.
- Expired, revoked, wrong-node, wrong-model, wrong-role, altered-attempt and
  superseded-epoch bindings are denied at the fake provider-call boundary.
  Mock witness acknowledgement failure blocks the associated action.
- Synthetic webhook, cron, messages, DLQ replay, mailbox, and crew paths
  cannot invoke the fake call site; only explicit router-origin is accepted.
- This is **not an integration into Mercury**: no actual streamText SDK
  interception, socket, cross-node authenticated renewal, physical quota
  provenance, hosted request, production router authority, independent WORM
  trace preservation or genuine deliverable-quality judgment was exercised.

**Disposition:** remain draft, disabled by default, no live free-provider
experiment. Next implementation is a pinned-upstream, isolated **offline**
provider-call-site wrapper with simulated stream/abort behavior. Do not
reassign free-provider checkpoint status based on this fixture pass.
