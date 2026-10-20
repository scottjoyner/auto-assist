# Mercury pinned streaming gate — offline-only integration experiment

**Date:** 2026-10-08 (America/New_York). **Status:** PRE-REGISTERED RESEARCH;
no live-generation or production authority. **Source pin:**
`cosmicstack-labs/mercury-agent@1be98293ace567092b961346d95363284f6e71cf`.
**Base:** scottjoyner/auto-assist PR #130 / knowledge PR #51.

## Scope, hypothesis, and expected results (before new tests)

Patch `src/bots/bot-turn.ts` in a separate, pinned upstream Mercury
worktree so its **actual** `streamText(` callsite is replaced with an injected
synthetic stream boundary. This is deliberately a *deny-by-default offline
research fork*, not the production provider SDK integration; no actual LLM
transport may be imported or executed. An explicit router-owned fixture
permit, independent exact expected binding, mocked authority and custody
acknowledgement, and fixture-only stream factory are mandatory.

- **S1 / entry:** Missing/disabled fixture, wrong task/node/model/role/group,
  attempt, epoch, expiry, or insufficient reservation denies before the
  fixture factory is invoked (zero launches).
- **S2 / ongoing:** The central mock lease is renewed before each emitted
  stream event and periodically during stalled streams; revocation,
  coordinator loss, expired lease or parent cancellation stops consumption
  and aborts the fake transport, including during an uncooperative
  iterator's pending next() call.
- **S3 / custody:** A rejected or unavailable mock witness acknowledgement
  denies dispatch, and post-admission failure cannot silently continue;
  synthetic receipts must identify the lease and task.
- **S4 / ingress:** The patched bot-turn has one controlled callsite and
  has **no** imported or direct `streamText` fallback; existing direct
  Mercury message/run/webhook/cron/replay paths lack the required injected
  permit, so are denied, not silently admitted.
- **S5 / regression:** The Python mock-boundary suite from PR #130 stays
  at or above 69 passing tests. The native Node no-network test harness
  passes for the new TypeScript guard; `git diff --check` is clean.

## Limits and governance

This is **source-level** instrumentation and a **standalone** TypeScript
fake-stream contract exercise, not an end-to-end Mercury BotManager,
AI SDK `streamText`, real tool callback, device, or provider cancellation
test. Upstream npm dependencies are not installed; avoid `npm install`,
`npm start`, credentials, daemon startups, router hooks, fleet mutations,
paid fallback and live model calls. A future full build and realistic SDK
fake transport require separate recorded acceptance. No independently
authenticated multi-host lease, WORM custody, provider budget provenance or
quality evaluation is established. Retain
`FREE-PROVIDER-LEASE-NEXT-CHECKPOINT=CHECKPOINT_NOT_REACHED`.

## Evidence procedure

Run native `node --experimental-strip-types --test` against the pinned
fork's mock gate suite. Keep the patch plus standalone tests with SHA-256,
reproduction steps, observed counters, failure modes and LaTeX updates
in the auto-assist research PR. Neither operator approval for continued
mock work nor passing tests authorizes live free-provider generation.

## Observation — October 8, 2026, after preregistration

**Result:** 12/12 native Node fixture tests passed, plus the 69/69 Python
offline mock-admission regression tests. `node --experimental-strip-types
--check` parsed the patched `bot-turn.ts`; `git diff --check` passed.
The patch was verified with `git apply --check` against the untouched,
pinned upstream checkout. No npm dependencies were installed; no Mercury
daemon, AI SDK, free or paid model inference, credentials or route changes
were activated. Aggregate is **81 focused offline checks** across two
different suites, not 81 end-to-end Mercury integration tests.

**Evidence files:**
- `research/mercury-pinned-offline/mercury-deny-only-stream.patch`
  SHA-256 `d21774ca671481a1b303684dd769ab596fa7bc82db31dac32f5cc380548a8df9`.
- `research/mercury-pinned-offline/research-provider-gate.ts` SHA-256
  `9858c8e9d9a8016a837520416d4968957476bfed71c6e3b9aa1f07e83ae486a4`.
- `research/mercury-pinned-offline/research-provider-gate.test.ts`
  SHA-256 `ef4877997cbc05654e6e94fee13b866ab4e2cbf74adfbabc95c525f4cf2a8945`.

**Observed positive cases:** exact fixture identity and full reserve binding;
witness acknowledgement before fixture launch; renewal before the first and
subsequent stream events; periodic renewal during no-delta stalls; bounded
startup coordinator silence; parent cancellation; mid-stream revocation;
local TTL expiry during stalled renewal; malformed factory rejection;
and idempotent lease release in this mock wrapper. All alternate Mercury
BotManager ingress paths still lack an injected permit by default in this
research-only fork. The source check confirms no direct `streamText(`
fallback remains in the patched `bot-turn.ts`.

**Observed research correction:** the first native test invocation had five
failing fixtures because the test supplied a generator *method* instead of
an async *iterable* stream. Correcting the fixture constructors made those
tests exercise actual iterator behavior; subsequent 12/12 focused results
are green. The initial failure was test-harness construction, not proven
runtime cancellation behavior.

**Still NOT proven:** an actual installed Mercury BotManager call, ai SDK
stream cancellation, SDK onStepFinish semantics, toolkit callbacks, end-to-end
retention, quota provenance, authenticated multi-host control, real credential
custody, independent WORM witnessing, useful accepted work, or physical
negative admission. Because the patch deliberately removes all reachable
`streamText` calls in the bot turn and only accepts a caller-injected
fixture, it is **not deployable as production inference**. Real SDK
instrumentation is a separate future review and needs a new, explicit
operator decision for live calls.

**Next read-only falsification:** run a clean dependency-enabled build of the
pinned fork in a network-isolated environment with a fake SDK transport and
memory-only model, then exercise actual patched BotManager injection and
the entire queue/webhook/cron/peer/replay call graph; separately prove
authenticated revocation and independent custody before requesting any
live free-provider trial. No such build has been performed in this slice.
