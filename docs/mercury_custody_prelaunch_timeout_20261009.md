# Mercury synthetic witness / prelaunch lease hardening — October 9, 2026

**Observed:** 2026-10-09 13:20–13:25 EDT, Fleet Commander on `x1-370`.
**Scope:** stacked, **draft-only** follow-up to [auto-assist PR #130](https://github.com/scottjoyner/auto-assist/pull/130); exact upstream Mercury pin `1be98293ace567092b961346d95363284f6e71cf`. No installation, webhooks, AI SDK calls or providers.

## Failure hypothesis and reason

The existing offline `startOfflineResearchRound` checked mock `authority.renew` with a 250 ms timeout, but awaited `authority.acknowledge` with **no timeout or parent-abort race**. A hanging witness could indefinitely suspend admission; a slow affirmative witness could arrive after the initial mocked lease expired or was revoked, and the fixture factory could launch without consulting the coordinator a second time. A hanging `release` on this failure path also blocked return.

These are mock-side timing bugs, not observations of vendor or Mercury runtime behavior. The hold-point is at the *fixture* launch path. Proving synthetic denial here cannot authorize external generation.

## Change and decision rationale

- Use a bounded 250 ms call around mock initial lease renewal, witness acknowledgement and post-witness lease renewal. Parent cancellation terminates the preflight wait.
- After acknowledgement, compare current fixture-clock expiry and exact identity again, and **reacquire/renew the same mock lease before fixture launch**. A revoked/expired/changed lease denies; affirmative historical custody alone is not live authorization.
- Make cleanup release bounded and attempted even after parent abort; unresolved release remains unverified rather than claiming a provider slot was returned.
- Do not introduce a live `streamText` fallback, retry, model tokens, new Mercury tasks, autonomous peer agents or paid-provider route.
- Keep the standalone gate and patch synchronized; no production code installation.

## Observations — offline acceptance

| Check | Result |
| --- | --- |
| Node 22 native TypeScript fixture regression | **18/18 passed**, up from previous 12/12 |
| Python synthetic lease/admission suites on PR #130 base | **69/69 passed** |
| Aggregate (independent mock suites, not integration tests) | **87/87 passed** |
| Added negative cases | Unbounded witness, abort during witness, revocation during witness, post-witness expiration, coordinator hang on revalidation, release cleanup hang |
| Model/provider requests | **0**, fixtures only |
| Mercury BotManager or real AI SDK invocation | **Not tested** |
| Exact npm dependencies installed | **No** |

Reproduce without remote access or model tokens:

```bash
# With a clean checkout already pinned to Mercury 1be98293ace567092b961346d95363284f6e71cf:
git apply --check /path/to/research/mercury-pinned-offline/mercury-deny-only-stream.patch
git apply /path/to/research/mercury-pinned-offline/mercury-deny-only-stream.patch
node --experimental-strip-types --check src/bots/research-provider-gate.ts
node --experimental-strip-types --check src/bots/bot-turn.ts
node --experimental-strip-types --test test/research-provider-gate.test.ts
# From the auto-assist research worktree:
python3 -m pytest -q tests/test_mercury_shadow_specialists.py tests/test_mercury_mock_authority_acceptance.py
```

**SHA-256 for reproducibility:** patch `953b1fca2c52e6f607c045531547de8c269796b212395513c41058f506f3c5dc`; gate `fd4be0341691b012630fdb5ea0245db81f78db2a38cb5d1f020a6b30bd9e62c1`; native tests `b1fbfb39018aed680cf830163b292beb84784ae683538b953bcab023321165f2`.

## Remaining blockers and nonclaims

**P0:** No authenticated, cross-host actual provider-group lease; no production-grade vendor quota authority, actual SDK cancellation or Mercury BotManager bypass inventory; witness is mock-only and cannot establish WORM/NAS custody. The upstream free-provider checkpoint is still `CHECKPOINT_NOT_REACHED`. The 250 ms timeout is an offline test constant, **not** a recommended production SLA. A late mock coordinator operation may still settle after local cancellation; a real protocol needs cancel/fencing tokens and reconciliation receipts.

**P1:** The streaming wrapper exposes fake source `text` and `finishReason` promises separately from the guarded event iterator; a future integration must bind every output/tool callback to lease and source authority, test asynchronous completed result access under revocation, and verify independent output quality. Cleanup timeout is a liveness guard, not proof of remote release. Source-level no-`streamText` fallback in the patch proves only a deny-only research fork, which is **not suitable for deploying real inference**.

**Next gate:** run a dependency-enabled, network-isolated Mercury BotManager with an entirely fake provider SDK; map webhook/cron/peer/queue/replay ingress and cancellation, then demonstrate independently witnessed trace receipts before requesting new live access.

**Rollback:** abandon/revert this stacked draft research branch. No active service configuration or primary repository main worktree was modified.