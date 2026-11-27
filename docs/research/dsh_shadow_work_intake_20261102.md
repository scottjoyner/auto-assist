# Starred DeepSeek superagent — safe shadow intake for productive inference

**Date:** November 2, 2026 (America/New_York) · **Owner:** AssistX research
· **Status:** DRAFT / PRODUCTION NO-GO.

## Why this candidate matters

The public `scottjoyner` starred list contains **1,068 repos**
(page 11: 68, page 12: empty in the current audit). The user goal is
continuous, **productive** inference across local GPU/NPU/CPU capacity
and free approved providers, not running synthetic benchmark loops
merely to keep utilization counters high.

[deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype),
revision `6337fc823ce20251c8de8085948165c81aaa701e` reviewed
from GitHub, is MIT licensed. **Its README explicitly says this is a
research-only prototype for reference, not an official DeepSeek
product, and not production-ready.** None of its packages, workers,
CLIs, GitHub webhooks, Feishu integrations, dashboards or scripts
were installed or started.

The pertinent upstream documents:
- `docs/000-philosophy.md`: act only when asked or under a written
  rule, avoid duplicate work and notification volume, keep humans
  in control.
- `docs/001-domain.md`: Source → Trigger → Eval → Ticket/Mandate →
  Assignment. Event identity and source cursor survive restarts in the
  fully fledged design. Tickets receive subscriptions as **context**
  without inheriting fresh authorization.
- `docs/002-authorization.md`: policy version, actor permissions,
  source identity, explicit versus inferred requests,
  and revalidation before resuming.
- `docs/003-execution.md`: worker slots, background jobs, bounded
  execution and human review stages.

These are **concepts to borrow**, not implementation instructions
to start an unreviewed long-running, OS-user agent.

## Existing AssistX contracts: avoid duplicate control planes

AssistX already provides a `TaskAuthority` schema,
`runtime_admission.py` profiles and live admission checks,
passive events, RQ task queue, and many auto-router integration
and owner authority guards. This draft does **not** replace,
wrap, or bypass those mechanisms. It is only a pure
**pre-intake research evaluator**, not a second scheduler.

## Files added

`src/assistx/proactive_work_shadow.py`

- Deterministic Python stdlib-only `fingerprint(policy)` and
  `evaluate(policy, event, state)` on **structured metadata only**.
- Strict github-only source/repository scope, canonical policy
  schema, policy version and SHA-256 pin, explicit allowed human
  and bot identities. Bots may propose work but cannot become a
  person's explicit request.
- Requires caller-provided source binding / actor verification
  indicators, source event ID, increasing source sequence and
  a supplied de-dup history. Unknown source, actor, kind, missing
  policy version, unexpected fields, malformed booleans, old cursor,
  duplicate event, or untrusted policy pin fails closed or
  returns a HOLD/IGNORE decision.
- A message on a thread with an existing owner is classified
  **CONTEXT_ONLY**, not a new execution mandate.
- Human direct/mention requests can produce
  `EXPLICIT_SHADOW_CANDIDATE`; human unaddressed and allowed bot
  notifications produce `INFERRED_SHADOW_CANDIDATE`.
  **Neither is authority to create a Ticket or dispatch work.**
- Does not accept raw comments, prompt bodies, shell commands,
  tools, model names, credentials, inferred approvals, cost
  estimates, token counts or execution directives as input.
- Receipts expose only digest, decision enum, short reason codes,
  and a conspicuous `production_gate=HOLD` plus false
  dispatch/mutation/notification flags. No source content or
  actor names are logged by this evaluator.

`tests/test_proactive_work_shadow.py` exercises real
imported code with synthetic source metadata. Exact
GitHub-branch source commit:
`6cceeebee4e418a2be93c2090036144472f65e0d`.

**74 / 74 focused tests PASSED** on x1-370 (Python 3.12,
stdlib function under pytest); AST parsing PASSED. Cases cover
explicit and inferred separation, bot impersonation,
unknown actor/CI refusal, policy-off, context-only
subscriptions, source replay and duplicates, policy changes,
unknown webhook provenance, invalid event types including
dict/list inputs, forged policy fields, unsigned runtime
"approval" attributes, bounded snapshots and output privacy.

The initial adversarial test discovered a malformed
list/dict kind could raise Python `TypeError` instead of
`IntakeRejected`. It was fixed before the final result.
No production events were consumed.

## Representative synthetic outcomes

| Input metadata | Result | New agent task? |
| --- | --- | --- |
| Source-verified, allowlisted human directly addresses agent; policy shadow | `EXPLICIT_SHADOW_CANDIDATE` | **No** |
| Allowlisted bot creates a suggested task | `INFERRED_SHADOW_CANDIDATE` | **No** |
| Existing Ticket owns the thread | `CONTEXT_ONLY` | **No** |
| Event ID replay / old cursor | `DUPLICATE_IGNORED` / `HOLD_REPLAY_OR_OUT_OF_ORDER` | **No** |
| Unverified source or actor | `HOLD_UNVERIFIED_SOURCE_OR_ACTOR` | **No** |
| Policy version or fingerprint mismatch | `IntakeRejected` / `HOLD_POLICY_FINGERPRINT` | **No** |
| Unlisted actor or CI | `IGNORED_UNAUTHORIZED_OR_PASSIVE` | **No** |

## What remains unverified / blocked

- **Critical:** source / actor verification flags and policy hash
  supplied by the caller are **not authenticated** by the module.
  An untrusted caller can forge these values; therefore even an
  explicit shadow candidate **is not a real human mandate**.
- No signed immutable event receipt, independent source cursor
  custody, active Ticket/mutual exclusion, owner/lease, retry,
  durable dedup, approval workflow, model availability, rate
  limits, provider spend cap, or SGNC attestation is wired.
- No actual issue or pull-request work was executed;
  no models loaded; **0 prompt/decode model tokens generated**;
  no fleet utilization increase is claimed.
- A production-ready gate must also verify the GitHub webhook's
  origin/signature and actor identity, snapshot policy versions
  under trusted custody, enforce exactly-once work and require
  a separately signed task mandate with allowed repositories,
  capabilities and explicit cost/CPU/GPU budgets. It must
  never treat instruction text inside comments, issues, code,
  docs or other untrusted data as changes to approval policy.
- Evaluation and work dispatch must remain separate components
  and the existing AssistX owner/router must retain
  sole execution authority. Long-running work must be
  useful to the project and **not** intentionally consume
  capacity merely to inflate model utilization.

## Safe next engineering gates

1. Read-only compare synthetic shadow decisions against
   previously archived, **non-sensitive** owner-authorized
   event metadata after its custody is independently verified.
   Reject unproven or replayed sources.
2. Connect only to an existing **trusted** AssistX task intake
   at a separately reviewed API boundary. Include durable
   idempotency, mandate scope, source checkpoint, expiration,
   policy rollovers and revocation.
3. Classify small real tasks with a verified positive utility,
   determinable owner and bounded costs (e.g. docs/test
   suggestions). Keep unsolicited bot work in shadow mode.
4. Compare useful accepted tasks, PR quality, acceptance rates,
   feedback volume, actual input/output tokens, time to
   completion and duplicate prevention across the existing
   free/local models, while preserving SGNC traces and
   Auto-Router admission policy.
5. Require **explicit separate approval** for enabling
   live GitHub ingestion, sending comments, modifying repos,
   installing runners or dispatching any inference.

**Recommendation:** keep DSH as an architecture reference and
review this as a **draft-only, side-effect-free** helper.
Do not deploy its standalone workers or automatically create
Tickets based solely on policy and event claims.
