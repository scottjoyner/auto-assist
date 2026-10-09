# Mercury provider-call admission gate — source-pinned offline follow-up

**Date:** 2026-10-09 (EDT, America/New_York); observed approximately 13:49–14:01 EDT.
**Status:** isolated source-patch research only; feature not installed, enabled, deployed, or authorized for live inference.
**Operator directive:** continue pushing specialization integration beyond the fixture-only Python adapter.
**Source pin:** [cosmicstack-labs/mercury-agent](https://github.com/cosmicstack-labs/mercury-agent) at `1be98293ace567092b961346d95363284f6e71cf` (v1.3.0); **do not apply blindly to a different revision**.
**Prior context:** [knowledge PR #51](https://github.com/scottjoyner/knowledge/pull/51) and [auto-assist PR #130](https://github.com/scottjoyner/auto-assist/pull/130).

## Implemented change: actual Mercury BotManager-to-SDK path

Reproducible patch: `integrations/mercury/mercury-v1.3.0-fleet-provider-admission.patch`.

- `src/bots/fleet-provider-admission.ts`: injected `FleetLeaseAuthority` contract `acquire/renew/release`, exact task/bot/provider/model/physical upstream-group binding, strict expiration/TTL, denial of unknown groups and invalid task IDs, watchdog renewal while a stream is quiet, irreversible abort on renewal failure and required release before completed status. **No built-in HTTP client, vendor tokens or credentials**; actual coordinator authentication remains unimplemented.
- `src/bots/bot-manager.ts`: optional injected `BotManagerDeps.fleetAdmission` with trusted exact-group resolver and bounded input/output reservations; every bot turn constructed through this BotManager instance receives the scope. An unconfigured upstream Mercury installation remains unchanged — **not a global enforcement hook**.
- `src/bots/bot-turn.ts`: acquire before **actual** `ai.streamText`; renew before every generation round, pass `AbortSignal.any` to SDK, monitor lease validity per received event and during the silent interval; set SDK output limit, stop after observed token-reservation overruns; guarded scope currently disables **all tools**; require final release before claiming completion.
- A denied lease is classified `fleet_provider_admission_denied`, which is not included in `isTransientFailure`; BotManager's standard retry loop therefore cannot silently auto-retry a denied task. A task cancelled by guard's own signal likewise receives the permanent admission-denied classification.
- `src/bots/fleet-provider-admission.node.check.mjs` tests standalone admission policy with Node 22 type stripping; `src/bots/bot-turn.test.ts` adds eight Vitest tests at the real mocked `streamText` call boundary.

## Reproducible acceptance and observed results

On `x1-370` using isolated Mercury worktree `/home/scott/git/wt-mercury-source-provider-guard-20261009`:

```bash
# Verify exact pinned clean upstream can accept the patch without mutation
git -C /home/scott/git/upstream-mercury-agent-20261008 apply --check \
  /home/scott/git/wt-auto-assist-mercury-shadow-20261008/integrations/mercury/mercury-v1.3.0-fleet-provider-admission.patch

# Test the already-patched isolated source worktree (no model calls)
cd /home/scott/git/wt-mercury-source-provider-guard-20261009
node --experimental-strip-types --test src/bots/fleet-provider-admission.node.check.mjs

# Vitest runs with dependencies from an existing, separate OmniRoute local installation.
# Exact Mercury npm dependency lockfile installation remains pending.
./node_modules/.bin/vitest run src/bots/bot-turn.test.ts --reporter=dot

# Scoped typecheck when an existing TypeScript >=6 compiler is available.
./node_modules/.bin/tsc --ignoreConfig --noEmit --skipLibCheck \
  --target ES2022 --module ESNext --moduleResolution bundler --types node \
  src/bots/fleet-provider-admission.ts
```

**Observed, not projected:** 17/17 Node lease tests passed; 16/16 BotTurn Vitest tests passed (8 existing + 8 new); standalone admission TypeScript typecheck passed; `git apply --check` passed on the pinned upstream with no changes. A first test discovered that task-ID validation accepted path-like characters, which was corrected and regression-tested.

**Not green:** Full Mercury project typecheck blocked by missing packages from the substituted dependency tree. Running `bot-manager.test.ts` failed at module loading because `node-cron` was missing; it did not execute the test suite. Borrowed OmniRoute dependencies included `ai@6.0.225`, `vitest@4.1.10`, `typescript@6.0.3`, while Mercury declares e.g. `vitest@^3`, `typescript@^5.7`. A clean Mercury lockfile-based install/build/test matrix and current-Mercury-head compatibility remain **unverified**. No dependency installations or provider calls were performed in this slice.

## Live activation remains prohibited: important gaps

1. **P0**: The lease interface is an injected contract, not a real authenticated, durable, cross-node service. The exact physical-upstream alias-to-quota mapping, coordinator identity, quota reservation against vendor allowances, and signed audit receipts remain unproven.
2. **P0**: Only BotManager-run `runBotTurn` flows are scoped. Mercury's main `src/core/agent.ts`, `src/core/sub-agent.ts`, provider helper methods, persona refinement, skill synthesis and other model call paths are **not protected** by this patch. Do not expose shared-credential provider routes or autonomous spawning before a full call-site and retry audit.
3. **P0**: Watchdog-driven `AbortSignal` cancellation was simulated; no real LLM SDK transport latency/response-tail experiment was conducted. No tool execution is allowed in gated mode until independently verified per-tool pre-execution admission and cancellation.
4. **P0**: Our local append-only shadow journal and Mercury's rotating journal do not establish independent off-host WORM custody or NAS restore proof; a complete trace/receipt bridge is required.
5. **P1**: Budget tracking after observed usage is an overrun detector, **not** proof the vendor cannot charge additional tokens during a streaming step. Exact provider request limits, signed provider usage and separate quality acceptance are still mandatory.
6. **P1**: Partial project toolchain is not the pinned full dependency set. Resolve/install dependencies in a separate capacity-approved scratch location, run Mercury's actual full tests and typechecks on the exact-head patch; do not alter in-use production modules.
7. **P1**: No systemd service, Mercury process, webhook, API router route, paid endpoint, iPhone app or fleet policy was touched. The feature is disabled by absence of injected authority and has no route for live use.

## Next shortest high-ROI slice

Treat the attached patch as **research evidence**, not an approved deployable artifact. Install the pinned Mercury dependency lockfile on a low-pressure compute node or isolated cache, complete BotManager and cross-path tests, then implement a fake authenticated upstream authority and a true SDK transport mock that observes cancellation with tools denied. Require all relevant entry points to fail closed in a dedicated fleet mode before any live free-token canary. Preserve existing `FREE-PROVIDER-LEASE-NEXT-CHECKPOINT.md` decision boundary and explicit operator authorization requirements.

**Rollback:** no running service requires rollback. Revert/unapply this unmerged patch or delete the isolated branch; do not affect other providers, agents, NAS mounts or their audit logs.
