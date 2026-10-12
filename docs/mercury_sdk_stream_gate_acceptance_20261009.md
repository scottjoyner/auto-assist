# Mercury Agent: actual AI SDK stream mock, full-suite acceptance and regression correction

**Observed:** 2026-10-09 14:08–14:15 EDT, Fleet Commander on x1-370.
**Research only:** no provider credentials, hosted inference requests, production services, model tokens or fleet route changes. No activation authorization.
**Source pin:** Mercury Agent v1.3.0 `1be98293ace567092b961346d95363284f6e71cf`.
**Prerequisite:** [auto-assist draft PR #225](https://github.com/scottjoyner/auto-assist/pull/225), which introduced the first patch; this document and **v2 patch supersede that patch for further experiments**, not the underlying governance.
**Patch:** `integrations/mercury/mercury-v1.3.0-stream-guard-v2.patch`. Applied to *pristine* upstream with `git apply --check` PASS. 6 Mercury files, 647 additions, 5 deletions, including new integration test.

## Hypothesis and observed acceptance

| Hypothesis | Observation |
| --- | --- |
| Invalid/missing lease cannot invoke the actual AI SDK model transport. | PASS: `ai/test` `MockLanguageModelV3` remained at **zero `doStream` calls** on denied admission. |
| A valid lease permits a zero-tool stream only and requires successful final release. | PASS: real `ai.streamText` completed a mock text stream with model attribution, one `doStream` and verified mock acquire/renew/release calls. |
| An authorized stream that goes quiet can still be revoked before any next chunk arrives. | PASS: injected second-stage fake coordinator returned denial, lease watchdog aborted the real SDK's abort signal, and a mock transport listening to the signal terminated; outcome not completed and categorized `fleet_provider_admission_denied`. The experiment measured an approximately **2.0-second** watchdog path due to the 1-second default interval and scripted sequence; this is not a guaranteed physical network cancellation bound. |
| Ordinary unguarded Mercury concurrent delegation must not regress. | A regression WAS FOUND: optional `await fleetTurn?.renewOrRevoke()`, `await ...close()`, and `await ...dispose()` introduced async microtasks even without a fleet lease. Original `fleet-delegation.test.ts` failed (**1 vs expected 2 simultaneous bots**) on v1 patch but passed on untouched upstream. v2 replaces these with `if (fleetTurn) await ...`; targeted suite **22/22** and full suite **786/786** passed. |
| Full Mercury project tests can run with pinned dependencies. | PASS: a dedicated `npm ci --ignore-scripts --prefer-offline --no-audit --no-fund` on the isolated worktree, followed by `npm run postinstall` (Ink patch) and `npm rebuild better-sqlite3`; full suite **110 files, 786 tests passed**, 2-worker bounded execution. All installs/patches affected only the isolated checkout's `node_modules`. |
| Full project typecheck should have no newly introduced errors. | PARTIAL: both pristine upstream and patched checkout produce **exactly 5 identical TS2322 errors** in existing UI/Ink list `itemKey` props; baseline/patched logs are byte-identical. These remain an upstream project blocker. |
| Node lease-fault tests remain valid. | **PASS: 17/17** native Node lease tests rerun against v2 at approximately 14:16 EDT, after the 786-test full Mercury run. |

## Reproduction (no live model or token usage)

```bash
BASE=/home/scott/git/upstream-mercury-agent-20261008
PATCH=/home/scott/git/wt-auto-assist-mercury-sdk-transport-20261009/integrations/mercury/mercury-v1.3.0-stream-guard-v2.patch
git -C "$BASE" apply --check "$PATCH"

# This isolated checkout already contains the patch and its own install.
cd /home/scott/git/wt-mercury-stream-guard-20261009
node --experimental-strip-types --test src/bots/fleet-provider-admission.node.check.mjs
npm test -- --maxWorkers=2
npm run typecheck
```

**Evidence logs (local):** `/tmp/mercury-full-vitest-postfix-20261009.log`, `/tmp/mercury-typecheck-20261009.log`, `/tmp/mercury-typecheck-baseline-20261009.log`, and `/tmp/mercury-fleet-regression-postfix-20261009.log`.
**Patch SHA-256:** `cb0ee3377c24fc9d006db3df5fd84875929aaafa18555c307c445f8e3182619e` as generated 2026-10-09 14:15 EDT; record new digest after any revision.

## Risks and blockers intentionally carried forward

1. **P0 — incomplete call-site coverage:** The lease is opt-in within injected BotManager. `src/core/agent.ts` has several direct `streamText/generateText` invocations; `src/core/sub-agent.ts` and provider helper methods, persona refinement, onboarding, skill synthesis and alternate channels are **not** fleet-policy guarded. No fleet-wide free credential should be handed to an unprotected Mercury process.
2. **P0 — authority:** The mock callback authority is not a deployed authenticated cross-node lease service, and the test uses synthetic upstream identity rather than actual physical provider grouping, verified quotas or cryptographic audit records.
3. **P0 — cancellation strength:** `ai/test` transport listened to abort in memory; a real vendor/SDK transport can ignore cancellation, complete billing after revocation, or leave untrusted tool side effects. Zero tools in scoped mode is still required.
4. **P0 — custody:** The local Mercury journal rotates and the prototype hash chain is mutable; independent full-event custody, source receipt hashes and NAS5 restore proof are outstanding. Do not delete or compact original traces.
5. **P1 — command coverage:** Whole-program TypeScript typecheck has 5 proven upstream UI errors, unrelated to the guard. A clean upstream CI gate and UI fixes should be tracked separately.
6. **P1 — token promises:** SDK max-output caps and per-step observed usage are NOT authoritative pre-call free-provider billing or quota enforcement; accepted work quality is also unmeasured.
7. **P1 — storage pressure:** root/SSD limits and NAS5 recovery remain relevant; no worker services were installed or extra sessions launched.

## Next highest-value gate

Implement **fleet-mode admission completeness** across all model call sites (not just BotManager), with an authenticated mock coordinator, negative tests for each bypass route, and storage-backed, independently witnessed traces. Preserve a feature-disabled rollout and strict actual physical-group provider allowlist. Only then seek explicit operator authorization for a single observed free-provider canary. The previous `FREE-PROVIDER-LEASE-NEXT-CHECKPOINT.md` boundary is unchanged.

**Decision:** Keep both research patches draft. The v2 patch supersedes v1 for reproducing tests. No merge, deployment, paid fallback, extra real provider traffic or automatic bot spawning.
