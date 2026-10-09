# Mercury fleet-wide SDK model-call gateway — research acceptance

**Observed:** 2026-10-09 14:41–14:50 EDT (America/New_York), Fleet Commander on x1-370.
**State:** isolated opt-in, **fail-closed** research patch. Not installed or enabled on any Mercury service. No provider tokens, external inference, paid fallback, production routing, or NAS mutation.
**Source pin:** [cosmicstack-labs/mercury-agent](https://github.com/cosmicstack-labs/mercury-agent) v1.3.0, commit `1be98293ace567092b961346d95363284f6e71cf`.
**Supersedes experimental patch:** [auto-assist PR #226](https://github.com/scottjoyner/auto-assist/pull/226) only for *future research application*. Preserves ADR/free-provider checkpoint.
**Patch:** `integrations/mercury/mercury-v1.3.0-fleet-wide-model-gate-v3.patch`. Independent `git apply --check` PASS on untouched pinned upstream, 15 Mercury files, 888 additions and 13 deletions.

## Design: deny unadmitted calls, even outside BotManager

The prior source-pinned v2 guard only wrapped Mercury's `BotManager → runBotTurn → ai.streamText`. Many other entry points reach `ai.generateText` or `ai.streamText` without going through BotManager: main agent, subagent, OpenAI-compatible/Anthropic/Mercury Cloud provider helpers, Kanban and Workspace IDE web endpoints. Persona refinement, skill synthesis and related helpers call through the provider methods, so those inherited bypasses matter.

This patch introduces `src/core/fleet-model-gateway.ts`. Mercury's eight production files importing SDK `generateText` or `streamText` now import the gateway instead; the gateway alone imports those generation functions from `ai`.

- **When `MERCURY_FLEET_MODEL_GATE` is defined:** any unscoped `generateText` or `streamText` call raises `FleetAdmissionDenied` **before** entering SDK transport. Even an unexpected or `off`-looking value arms denial; absent is the *only* standalone mode. No live activation in this experiment.
- **Guarded bot turn:** the existing authenticated-*interface* (mock-only) `FleetTurnAdmission` is passed using an internal unique symbol in stream options; the SDK gateway verifies a genuine, non-revoked class instance, strips the symbol before invoking SDK, and refuses any attached tools.
- **Other pathways:** they are intentionally **blocked**, not redirected. This is a deny-only completion gate, *not* a functioning all-path agent harness with lease negotiation.
- **Standalone Mercury:** with env variable genuinely absent, SDK behavior stays unchanged. The new gate can only enforce if the process is launched with the flag and the exact code is used.
- **Regression-proof source scanner:** new TypeScript-AST test reads every production `src` file and fails if `generateText`/`streamText` are re-imported directly from `ai` outside the gateway, or a namespace/dynamic `ai` import is added. **This is a source-import audit, not proof against arbitrary dynamically loaded code/plugins or raw HTTP requests.**

## Measured tests on x1-370 (offline only)

| Acceptance | Observation |
| --- | --- |
| Full Mercury package typecheck, canonical dependencies | **PASS**, `npm run typecheck` exit 0 in this source checkout at ~14:48 EDT. Unlike earlier runs showing baseline UI errors, rechecking this environment was clean; do not infer an upstream code correction from this alone. |
| Targeted tests: new gateway, actual SDK mock stream, bot-turn regressions | **28/28 PASS**, 3 files, ~14:48:47 EDT. Nine new tests cover denied generate/stream before transport, unknown and misleading mode flags, forged/revoked scope, zero-tool permit, SDK mock stream and AST import inventory. |
| Full canonical Mercury Vitest suite | **795/795 PASS** across **111/111** files, `npm test -- --maxWorkers=2`, 14:49:04–14:49:23 EDT. Includes previously tested watchdog cancellation and concurrent fleet delegation. |
| Exact pin patch compatibility | **PASS**, `git apply --check` on original read-only checkout `1be9829`; full source patch can be applied to a clean clone without installing any Mercury service. |
| Real cross-node quota and trace archival | **NOT TESTED**; mock lease and in-memory SDK model only. |

The native Node lease-fault suite was independently rerun on v3 at 14:53 EDT: **17/17 PASS**. This remains an offline lease test, not external authenticated authority.

## Reproduce — never call a hosted model

```bash
# Existing pinned, isolated source with v3 changes and canonical npm install:
cd /home/scott/git/wt-mercury-stream-guard-20261009
npm run typecheck
npm test -- --maxWorkers=2
node --experimental-strip-types --test src/bots/fleet-provider-admission.node.check.mjs

# Verify exported research diff against untouched upstream:
git -C /home/scott/git/upstream-mercury-agent-20261008 apply --check \
  /home/scott/git/wt-auto-assist-mercury-fleet-gate-20261009/integrations/mercury/mercury-v1.3.0-fleet-wide-model-gate-v3.patch
```

Local result logs: `/tmp/mercury-fleet-gateway-tests-v3.log`, `/tmp/mercury-fleet-gateway-full-20261009.log`, `/tmp/mercury-fleet-gateway-typecheck-final.log`.

## Still blocked before live specialization or production

1. **P0 — fleet trust boundary:** environment-triggered SDK wrapper is a process-level guard, not a hardened isolation boundary; hostile dynamically loaded plugins, raw HTTP clients and module rewriting are not covered. Production must isolate credentials, authenticate coordinator and sandbox allowed worker code.
2. **P0 — actual authority:** no authenticated, externally durable lease coordinator, physical upstream alias/credential grouping, auditable quota reservations, renewal and revocation over a real transport. Setting fleet mode today will deny non-bot SDK routes; Boot `index.ts` does not inject an authority, so bot requests also deny until an explicit trusted installer connects it. Never treat env flag alone as activation readiness.
3. **P0 — transport cancellation:** previous mock SDK abort evidence is in-memory; no external vendor acknowledgement or billing stop bound. Guarded tools remain prohibited.
4. **P0 — full traces:** Mercury journal rotation and locally mutable hash-chain remain insufficient. Need independent append-only event receipts and NAS restore witness before compaction/deletion, mindful of NAS5 reconciliation.
5. **P1 — policy static completeness:** AST scanner covers imports of the `ai` named API, not arbitrary direct client invocations or third-party extensions; human review plus deny tests for alternate transports are still required.
6. **P1 — release/CI:** tested code is an additive research patch, **not an upstream Mercury merge**. Test against current upstream head, exact CI and properly configured host, then obtain operator approval for any one-slot live free-model canary.

## Next target

Implement an authenticated **mock-only** lease coordinator adapter, wire the fleet BotManager from a read-only, explicitly set boot configuration, enforce positive-list provider/model/upstream group and least-privilege read-only role permissions, and produce end-to-end deny cases from a webhook/queue to the gateway. The next gate is evidence of *enforced* cross-host identity, not additional speculative concurrency. Keep draft/unmerged and do not allow unattended Mercury dispatch or paid fallback.

**Rollback:** do not apply/merge the patch; existing live services have not been modified. Default Mercury remains unchanged with fleet env absent.
