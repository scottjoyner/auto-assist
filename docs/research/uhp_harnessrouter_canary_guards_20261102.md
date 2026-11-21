# UHP / HarnessRouter research-only canary hardening

**November 2, 2026 (Eastern)** · source review on x1-370 · **production HOLD**.

## Why this is a better starred-repository addition

[HarnessRouter/harnessrouter](https://github.com/HarnessRouter/harnessrouter)
is already represented by AssistX's
`src/assistx/harnessrouter_adapter.py`: an *optional, default-off*
execution-lifecycle transport. The right integration is to improve
that adapter's authority and provenance boundaries, **not**
install another HarnessRouter server or scheduler.

The active production routing, free-provider admission,
agent-harness locks, Hermes, Neo4j, SGNC custody
and model services were neither changed nor queried.
This branch is a draft. Nothing was merged or deployed.

## Upstream protocol pinned for this research

- GitHub source commit:
  `f6592b27e0d46478f895100a20827d60b538acf4`
  (November 1, 2026).
- The current upstream
  [Unified Harness Protocol 2026-10-19](https://github.com/HarnessRouter/harnessrouter/tree/f6592b27e0d46478f895100a20827d60b538acf4/protocol/versions/2026-10-19)
  is a **draft** standard.
- Official conformance package at that commit:
  `2026.10.19.post4`. Its README requires **101 checks** across
  core, extended and full. The full suite runs real agent tasks,
  verifies cancellation/streaming/file/artifact behavior, and
  consumes actual model tokens.

At the pinned commit, the upstream's **own** synthetic/loopback
tests `test_stream_framing.py`, `test_failure_evidence.py`,
`test_report.py` were fetched directly from the exact GitHub commit
into a disposable temporary Python directory and run with local
`pytest` and `jsonschema`: **33/33 PASS** (4.74 seconds).
The suite's client, checks, context, registry, report, and
version-specific schema were pinned to the same source.
No real agent, paid provider, model or UHP server was contacted.
These 33 tests are **not** a 101-check conformance result.

The existing AssistX canary adoption document cites older
HarnessRouter v0.31.5 / UHP `2026-10-04`.
**Do not silently promote** that integration to
UHP 2026-10-19 or any conformance class.

## Actual adapter findings

The main-branch adapter already required
`canary_enabled=true` and explicit per-call opt-in
for task submit and cancellation, but it contained
several source-level gaps that matter to
multi-harness fleet operation:

| Existing behavior | Risk | Draft guard |
| --- | --- | --- |
| Cancellation trusted a supplied allowlisted harness ID, without binding its response ID | A caller who knew a different task ID could request its cancellation using an allowed harness | Track returned response IDs *in-memory* per adapter instance, bound to their original harness ID; refuse unknown/mismatched IDs before networking |
| Empty configured allowlist admitted any harness when other flags were true | Mutation scope was too broad | Mutations require a nonempty exact harness-ID allowlist |
| GET response polling accepted arbitrary ID | Read-only cross-session data access using the same client credentials | Poll only IDs locally submitted by that adapter instance |
| Missing/unknown status defaulted to `unknown` | Hidden state mismatch and unreliable admission/accounting | Enforce five UHP states: `in_progress`, `completed`, `failed`, `incomplete`, `cancelled`; failed response must include error |
| Token counts converted arbitrary values via `int` | Booleans, negative values and strings could become false model-utilization receipts | Accept only nonnegative integer token counts, or absent |
| Model response could silently differ from requested | Wrong engine/provider/quality/billing attribution | Reject substitution lacking `requested_model`, `model_fallback=true` and reason |
| URL path accepted user-supplied response ID with path separators/query text | Request confusion/URL path injection | Require exact canonical unescaped ASCII response identifier |
| Untrusted HTTP error body echoed into exception | Credentials/prompts/host detail could be reflected into logs | Expose HTTP status only, not raw response body |
| Sensitive raw receipt appeared in dataclass `repr` | Accidental exception/log disclosure | Omit raw/error data from receipt `repr` |
| No explicit UHP version discovery method | An older server could be mistaken for the current contract | Read-only `discover_protocol()` verifies discovery shape/version/capabilities and marks conformance **not verified** |

The adapter **still does not provide streaming**, full UHP
session sharing, artifact verification, complete audit receipts,
or model-token accounting independent of an upstream reporter.
A successful HTTP cancellation response only indicates an
acknowledgement: it is **not** proof that a background task
actually stopped. The full UHP suite's cancellation lifecycle
checks remain required.

### Intentional fail-closed limitation

The task-ID ownership map is **process-local**. After process
restart, an existing background canary response cannot be
polled or cancelled through the new adapter instance.
That is a deliberate conservative gate, **not** a complete
durable task ownership solution. A production design needs
independently authenticated, persistent ownership with
revocation, expiration, idempotency and crash recovery,
not a global bypass.

`discover_protocol()` reports the server's advertised
class and capabilities and whether its versions list
contains `2026-10-19`. This is **self-reported metadata**.
The output always includes
`conformance_independently_verified=false` and
`authority=discovery-only`.
It neither executes tasks nor installs a harness.

## Test evidence

At commit `a701db94c9566491e729d693f6f9405fa74f1dc9`,
the current GitHub-branch adapter, its **unmodified prior
test file**, and the new adversarial
`tests/test_harnessrouter_uhp_guards.py` were read via
GitHub and run in a disposable isolated directory
on x1-370 under Python 3.12.

**74/74 local tests PASS**, AST parse PASS. The tests
are synthetic and use injected in-memory transports.
They cover response ownership/cross-harness refusal,
empty allowlists, invalid path identifiers,
all five lifecycle states, negative/bool/string token
counts, failed-without-error, response-ID mismatches,
honest model fallback, raw-error redaction,
duplicate response-ID collision, old-version UHP
declaration, malformed discovery, and false
claimed extended capabilities.

The local active auto-assist checkout was **not**
checked out or modified; it remains on its separate
branch. No live tasks, traces, credentials or requests
to production HarnessRouter endpoints were performed.

## Future independent gates

Before any UHP canary deployment or merge:

1. Confirm old consumer compatibility and exact response
   wire contracts; add full suite regression CI and
   authorized synthetic server fixture where possible.
2. Implement authenticated durable ID-to-harness ownership,
   with per-principal scope, session continuity, restart
   recovery and explicit cancellation lifecycle verification.
3. Independently pin the target UHP version and probe
   a test-only UHP server that does not share production keys
   or fleet model scheduling.
4. Run the official **full applicable conformance suite**
   against that isolated test server only with explicit
   authorized task budget. Include gapless SSE sequencing,
   progressive flush, no credential leakage, file path
   isolation, retries/error envelope, and cancellation
   reaching a terminal state.
5. Keep AssistX as sole routing/admission authority;
   do not create a second Hermes/Auto-Router control plane.

**Draft, research only, production NO-GO.**
No model inference productivity uplift is claimed from
synthetic conformance tests.
