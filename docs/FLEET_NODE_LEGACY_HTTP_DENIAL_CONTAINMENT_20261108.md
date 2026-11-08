# Fleet agent: bound rejected-auth retries, protect privileged HTTP transport

**2026-11-08. Current-main source-only repair. Production Task admission HOLD.**

## Observed defect and verified source change

Five live `fleet-node-agent.service` instances on xwing, Deathstar, Destroyer,
Lenovo and OptiPlex previously returned **401** on their legacy Basic-backed
`/api/agent/tasks` poll, while current protected production API denies
unregistered machine Task access. The historical legacy worker treated a 401
or 403 as an ordinary empty Task list and retried at the configured polling
cadence. It also used urllib's ambient proxy and redirect handlers for Basic
requests. Neither problem can legitimately be fixed by copying shared
operator Basic credentials to nodes.

This source patch ports narrowly reviewed controls from draft PR #560
(denial backoff) and #547 (credentialed HTTP transport hardening), **onto
current `main`**, without merging older stacked authorization behavior:

- HTTP **401/403** on legacy Task GET explicitly increments a bounded,
  saturating denial counter and sleeps with exponential backpressure
  `min(300s, min(300s, max(1s, configured_poll)) × 2^min(attempt-1,8))`.
  Counter saturates at nine consecutive denials; success, ordinary HTTP
  errors or non-auth responses reset it. Nonfinite/negative/boolean polling
  intervals fail before any Router or Task call. Fast zero-second retries
  are disallowed on normal empty-poll paths too.
- If **either** `FLEET_NODE_TOKEN` or
  `FLEET_NODE_IDENTITY_APPROVED_ORIGIN` is present in the environment,
  including blank, the worker cannot enter the old Task loop. It raises
  an explicit HOLD *before reporting to Router or sending Task HTTP*.
  `_poll_once` also refuses an individual legacy Task request in this
  mode, even when valid operator Basic credentials are simultaneously
  present. A token is **identity only**, never Task claim authority.
- Requests carrying operator Basic, `Authorization`, `Cookie`,
  `X-Fleet-Node-Token`, or credential-like header names use an explicit
  **no-ambient-proxy, deny-all-redirect** urllib opener. Privileged
  successful bodies are limited to 1 MiB, error bodies to 64 KiB;
  exceptions are sanitized to their class name without URL, headers,
  credentials or tokens. Existing legacy operator Basic semantics are
  unchanged; those credentials must still stay restricted to operators.
- This changes **only the old worker's behavior when built and deployed
  under a separately approved release**; the current protected production
  checkout and running fleet processes are NOT restarted or modified by
  this PR. Worker source remains legacy and will not claim tasks under
  externally authorized machine identity.

## Offline test coverage and negative guarantees

- Earlier research's six auth-backoff tests brought onto the current
  branch and expanded to cover partial/missing machine tokens, operator
  Basic plus machine-token denial, nonfinite polling intervals, exponential
  cooldown, saturated cap, reset on non-auth response and forged Tasks
  in a 401 body never being claimed.
- Source-authored privileged transport regressions from earlier #547 cover
  cross-origin redirect, environment proxy, HTTP response caps and sanitized
  failures. The tests use synthetic credentials and socketless/stubbed HTTP.
  Two existing LM Studio bearer-auth test files were updated to use the
  reviewed no-proxy/no-redirect opener instead of intercepting `urlopen`;
  their test network is loopback-only and never the resident model server.
- In a 2 GiB/2 CPU unprivileged, `--network none --read-only --cap-drop ALL`
  container, **90 relevant tests passed** (plus three subtests). Two tests
  requiring an unavailable async plugin were excluded locally and are left
  to the full hosted repository CI, not represented as local PASS.
- A dedicated minimal-permissions CI workflow runs only local synthetic
  tests with no fleet credentials or Task API connections. Hosted full
  general unit/recovery/handoff and isolated runtime image smoke remain
  separate exact-head release checks.

## Why Task execution still cannot be enabled

This fix reduces **rejected request load when distributed after approved
release**, and closes an HTTP credential-redirection risk in source. It does
**not** register per-node principals, copy or rotate credentials, verify a
fresh owner-signed required-node roster/runtime grant, create any Task slots,
resolve resource-owned claim/COMMIT, or authorize the five physical agents.
Machine Task GET must stay **403** until the privileged route is separately
implemented and reviewed. Actual production checkout remains pinned at
`8e8605cfb8b863be5189b702cc13e12ace4b4434`; canonical generation
**896** is expired, WorkSupply reports zero approved slots, and the
independent root-owned Ed25519 verification key is absent.

The safe follow-up requires an authorized node credential owner to register
and rotate each independently scoped machine identity and an independent
runtime owner to sign a fresh current resource/roster grant. Only then
should a tightly scoped one-node supervised staging pilot test identity
200, legacy Task denial, zero unauthorized Task claims, cooldown,
resource-lease/COMMIT fencing and rollback. Do **not** reactivate the old
projection keepalive, give workers general operator Basic, or weaken
machine Task admission to suppress the 401 symptoms.

Tracks release blockers: auto-assist #501 (node auth), #284 (signed admission)
and #569 (physical model access). Old draft PRs #560/#547 remain distinct
research history unless explicitly closed as superseded.
