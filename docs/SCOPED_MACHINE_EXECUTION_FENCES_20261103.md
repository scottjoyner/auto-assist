# Scoped-machine heartbeat and result finalization — research-only contract

**Status:** Source-only draft, feature disabled, no API routes or machine-agent
callers, no production Task/lease/model/credential/signature changes.

## Why this matters for real fleet utilization

On November 3, 2026, live Fleet Commander checks show Auto-Router degraded,
zero enabled free hosted providers and one open circuit. AssistX still has
`WorkSupplyState.allow_background=false`, `total_slots=0` because the
canonical projection approval is expired. Five legacy node agents' exact
process credentials still get 401, and the router's admin GET endpoints
remain anonymously accessible inside the Tailnet. The guarded Herder
research path has no unresolved receipt but uses its own limited quota.

A new isolated machine claim contract in stacked draft
[PR #508](https://github.com/scottjoyner/auto-assist/pull/508) now holds the
WorkSupplyState write lock while checking node, target, generation, policy,
model artifact, principal approval, capacity and claim lease. The existing
legacy heartbeat and completion paths still have first-attempt and missing
claim-ID bypasses; accepting a node token on those paths would be unsafe.

## New code

`scoped_machine_execution.py` adds **unregistered**, default-disabled
`scoped_execution_transition()`, gated by exact
`ASSISTX_ENABLE_SCOPED_NODE_EXECUTION=1`. No FastAPI route, worker import,
operator credential fallback or production entry point has been added.

Both operations require a trusted server-side exact node policy with
`audience=targeted-llm-execution`, only `llm` capability, exact token epoch,
model artifact fingerprint and approved projection generation, plus a node
identity authenticated by a separate trusted caller. A producer cannot
self-authorize by simply submitting these fields. The executor locks the
canonical WorkSupplyState row in a single Neo4j write transaction and
rechecks:

- current approved canonical projection/manifest, matching generation and
  future expiry; fresh and positive WorkSupplyState;
- non-revoked MachinePrincipalAdmission with matching node, epoch and model;
  exact admitted runtime, chat model artifact, access path and capacity;
- Task with exact ID, target, claimed_by, session, claim ID,
  `execution_attempt=1`, token epoch, generation, expected Task approval ID,
  owner and current unexpired lease; no Paperclip or foreign allocation;
- status only `CLAIMED` or `RUNNING` (not READY/PAUSING/DONE).

A valid heartbeat may move the Task to RUNNING and renew a **bounded lease**.
A lost or forged claim, rotated token, revoked model/path, expired lease or
invalid request fails without extending lease or preserving lock writes.

## Result finalization requires separate custody

An HTTP 200, nonblank text, reported token count or computed result SHA
**does not equal accepted work**. This synthetic finalizer additionally
requires a *pre-existing* `TaskResultAcceptance` with exact node, Task,
claim, session, generation, token epoch, artifact and result SHA-256; explicit
`status=accepted`, verified-signature and external-anchor flags, a 64-character
checkpoint digest, an independent approver, expiry and an accepted timestamp
not preceding Task claim. Exactly **one** matching unconsumed receipt must
exist. One transaction atomically sets Task DONE and consumes that receipt;
replay/duplicate attempts cannot reset a terminal status or reuse a receipt.

**Critical boundary:** This module ONLY checks Neo4j metadata claiming
`signature_verified=true` and `external_anchor_verified=true`. It does **not**
perform cryptographic signature verification, verify the externally pinned
checkpoint, authenticate an independent custody service, or ensure a captured
model output actually matches the SHA. Those are mandatory external
components **before any production finalization**. Anyone able to write those
properties in Neo4j could counterfeit the synthetic acceptance gate unless
independent signing, storage, witness and strict writer ACLs are enforced.
Accordingly, the callable returns `transition_applied`, **not** a statement
that independent custody was proven, with
`external_custody_verified_by_this_module=false`.

## Verification

The deterministic unit suite uses fake transaction/identity clients and
verifies the feature flag, request/lease bounds, wrong/missing session/claim,
foreign node, old token, model mismatch, invalid receipt/digest and rollback.
The **real, disposable** `neo4j:5-enterprise` suite validates:

- a claimed Task heartbeats, then uses a synthetic matching acceptance record
  to finalize exactly once; a replay leaves the graph unchanged;
- 13 heartbeat-denial cases (wrong claim/session/epoch/owner/generation,
  expired lease, paused Task, revoked projection/principal/model/access,
  foreign allocation, wrong Task approval);
- 12 finalization-denial cases (missing/forged/reused/expired acceptance,
  wrong digest/node/epoch, same-node approver, missing external checkpoint,
  acceptance older than the claim);
- simultaneous finalizers accept **exactly one**, and two matching
  acceptance records with the same ID are refused rather than consumed.

All disposable test containers are removed; the live `neo4j` graph was never
used for write tests. Hosted checks must pass on the exact published branch
head before this source draft can be considered validated.

## Remaining P0 production blockers

1. Five currently running agents have rejected Basic credentials. They need
   owner-approved, least-privilege machine identities, not copied operator
   credentials. See Auto-Assist [#501](https://github.com/scottjoyner/auto-assist/issues/501).
2. Real MachinePrincipalAdmission, machine-approved Tasks, exact
   model/runtime/path/capacity evidence and the **fresh independently signed**
   canonical runtime projection are missing. Never mint, backdate or infer
   approvals from the test fixtures.
3. Legacy Task claim, heartbeat, completion, idempotent status rewrite,
   retries, Hermes and admission/revocation writers do **not all participate**
   in the canonical capacity lock. Revocation interleavings and global
   write/custody integrity are unproven. See P0 [#428](https://github.com/scottjoyner/auto-assist/issues/428).
4. Independent, cryptographically authenticated Task result acceptance,
   external anti-rollback custody and durable, replay-safe final receipts
   are still unimplemented. The source flag is **not** a substitute.
5. Auto-Router admin GETs still return HTTP 200 without authentication from
   x1 and other Tailnet nodes. A release cutover needs exact-image,
   credential-client and rollback gates.

No more production inference is authorized by this PR. Use existing models
for explicitly bounded research/code review, with recorded request tokens,
response quality and independent engineering verification; do not confuse
such research work with accepted AssistX Task completion.
