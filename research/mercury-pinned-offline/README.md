# Reproduce the pinned Mercury deny-only stream fixture (no model calls)

Source: cosmicstack-labs/mercury-agent at exact Git revision
`1be98293ace567092b961346d95363284f6e71cf` (MIT). This is a research
patch for a throwaway checked-out copy; never apply directly to a production
Mercury installation or invoke with credentials. No npm packages are needed
for the standalone mock streaming test.

In a **clean local clone already pinned to that revision**, with Node 22:

```bash
test "$(git rev-parse HEAD)" = "1be98293ace567092b961346d95363284f6e71cf"
test -z "$(git status --porcelain)"
git apply --check /path/to/mercury-deny-only-stream.patch
git apply /path/to/mercury-deny-only-stream.patch
node --experimental-strip-types --check src/bots/bot-turn.ts
node --experimental-strip-types --test test/research-provider-gate.test.ts
```

The test imports only a standalone TypeScript shim plus Node built-ins. A
static check confirms the pinned `bot-turn.ts` has no direct `streamText(`
call. No provider SDK is imported into the shim, and no Mercury server is
started. The mock authority and witness do **not** establish real leases,
vendor remaining budget, live revocation, WORM custody, or production
eligibility.

Patch SHA-256:
`953b1fca2c52e6f607c045531547de8c269796b212395513c41058f506f3c5dc`.

October 9 mock witness acceptance: 18 Node tests pass, with timed custody
acknowledgement, post-witness lease revalidation and bounded cleanup release.
See `docs/mercury_custody_prelaunch_timeout_20261009.md` in auto-assist.
The prior October 8 observations of 12 tests remain historical.

Not validated: dependency-enabled TypeScript typecheck of the full upstream
project, actual BotManager execution, SDK streaming or real network calls.
`FREE-PROVIDER-LEASE-NEXT-CHECKPOINT` remains `CHECKPOINT_NOT_REACHED`.