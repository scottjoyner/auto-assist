# Mercury Agent signed mock coordinator — queue-to-model acceptance

**Execution window:** 2026-10-09 15:42–15:52 EDT, `x1-370` via Fleet Commander.
**Status:** research patch only; **no live provider, production daemon, vendor secrets, paid fallback, remote authority, NAS changes, or fleet deployment**.
**Source pin:** Mercury Agent v1.3.0, `1be98293ace567092b961346d95363284f6e71cf`.
**Prior phases:** [PR #225](https://github.com/scottjoyner/auto-assist/pull/225) initial lease, [PR #226](https://github.com/scottjoyner/auto-assist/pull/226) SDK stream revocation, [PR #228](https://github.com/scottjoyner/auto-assist/pull/228) deny-only all-source SDK ingress.
**Patch:** `integrations/mercury/mercury-v1.3.0-signed-mock-authority-v4.patch`. This is a **complete, cumulative source patch** against the original Mercury pin; do not stack v1–v4 patches.

## What was built

`src/bots/fleet-mock-coordinator.ts` implements an *in-memory* mock authority with per-node 256-bit HMAC test keys, signed request/response envelopes, unique nonces with replay rejection, explicit node/bot/provider/model/physical-account-group allowlist, input/output reservation ceilings, one concurrent lease per upstream group, strict lease-ID ownership on renewal/release, simulated outage, expiry and revocation. Separate `MockSignedFleetClient` adapters share one `MockSignedFleetCoordinator` instance in tests. **No TCP server, token store, coordinator durability, actual mTLS, real quota API, or live provider execution exists**. HMAC alone does not supply safe distributed leadership.

`src/core/fleet-boot-guard.ts` refuses `runAgent` startup when the experimental fleet flag is present; the check runs **before** `registerRuntimeProcess`, so test-only authority cannot silently become production execution. Absence of the flag retains standalone Mercury behavior. Production `src/index.ts` never instantiates or injects the memory-backed mock authority.

The SDK gateway now checks a **module-owned WeakSet of successfully minted permits**, preventing an `Object.create(FleetTurnAdmission.prototype)` forgery from passing as a real lease. This is an in-process correctness check, **not** an adversarial sandbox: arbitrary code execution inside the process can still bypass wrappers and call transports directly.

`src/bots/fleet-mock-manager.integration.test.ts` runs two separate Mercury `BotManager` instances, **two disposable native SQLite queues**, and a single signed authority. The first admitted job stays streaming through the real AI SDK's mock language model while the other node submits a competing job. The second is denied before reaching `doStream`, then placed in the durable DLQ without automatic retry; after the first finishes, the shared slot returns to zero. No tools execute.

## Evidence and timestamps — October 9 EDT

| Clock | Observation |
| --- | --- |
| **15:42:43** | Inspected earlier source and worktrees, root 74% used, SSD_4TB 96% used; preserved original pinned source. |
| **15:43–15:46** | Added signed in-process coordinator fixture, strict permit allowlists and startup refusal before runtime registration. |
| **15:46:27–15:46:35** | Coordinator/security acceptance **16/16 passed** including two-node contention, tampering, replay, model/alias mismatch, partition, revocation, expiry, deliberate independent-authority split-brain witness, SDK positive/negative and startup modes. Typecheck passed. |
| **15:47–15:48** | Strengthened module-owned minted-permit check and tested prototype-fabricated lease denial. |
| **15:48:12–15:48:32** | Whole pinned Mercury suite **812/812 passed**, 112 test files; typecheck green. |
| **15:50:29–15:50:38** | Two independently queued real BotManagers + signed authority + real SDK mock `doStream` acceptance **1/1 passed**; second job permanently denied and journaled in DLQ. Typecheck passed. |
| **15:50:49–15:51:10** | Final whole suite **813/813 passed, 113/113 files**, bounded to two workers; native lease suite **17/17 passed**. |
| **15:51:23** | Exported cumulative 20-file v4 patch; `git apply --check` against clean upstream pin **passed**. |

## The key counterexample

Two **independent** memory-backed authorities with identical policy can each accept a request for the same upstream group. The test intentionally confirms this split brain. Signing alone does not fix distributed admission; production must guarantee one authoritative write path or equivalent consensus/epoch/fencing, and revoke any stale or partitioned issuer. This fixture is **never** a distributed failover solution.

## Operational acceptance matrix

| Required gate | This slice |
| --- | --- |
| Signed mock node/request/response identity | **PASS, in-process test only** |
| One slot contested by two BotManager durable job queues | **PASS, mock test only** |
| SDK model calls prevented for denied node | **PASS, mock SDK `doStream` 0 calls** |
| Runtime process boot under experimental fleet flag | **DENIED intentionally** |
| Complete canonical Mercury test suite and typecheck | **PASS, offline** |
| Authenticated/durable external authority with fencing | **NOT DONE** |
| Cross-host real transport + quota alias / billing proof | **NOT DONE** |
| Real provider transport abort / verified token budget | **NOT DONE** |
| Independent off-host trace custody & NAS recovery restore | **NOT DONE** |
| Credentials isolation / hardened plugin or raw HTTP bypass | **NOT DONE** |

## Reproduction

```bash
cd /home/scott/git/wt-mercury-stream-guard-20261009
npm run typecheck
npm test -- --maxWorkers=2
node --experimental-strip-types --test src/bots/fleet-provider-admission.node.check.mjs
git -C /home/scott/git/upstream-mercury-agent-20261008 apply --check \
  /home/scott/git/wt-auto-assist-mercury-authmock-20261009/integrations/mercury/mercury-v1.3.0-signed-mock-authority-v4.patch
```

Evidence: `/tmp/mercury-v4-coordinator-targeted.log`, `/tmp/mercury-v4-manager-20261009.log`, `/tmp/mercury-v4-full-20261009.log`, `/tmp/mercury-v4-native-20261009.log`, `/tmp/mercury-v4-manager-typecheck.log`.

**Next slice, not authorized for production:** replace the in-process fixture with a separately deployed *test-only* authenticated service, add epoch/fencing and physical-negative tests across x1-370 and xwing, then append signed decision evidence to independent storage. Real fleet runtime startup should remain blocked until the authority and trace custody are independently accepted. Paid-provider fallback, token-consuming canary, or service rollout require separate operator decision.
