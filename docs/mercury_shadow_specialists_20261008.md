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