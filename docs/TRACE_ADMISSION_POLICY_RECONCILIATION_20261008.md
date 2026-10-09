# OBS-RC1 — Trace read-admission policy reconciliation
Date: October 8, 2026 EDT
Status: research, release NO-GO; no deployment or merge authority.

## Competing draft policies
- PR #146, inherited by PR #150: default-disabled, HMAC-keyed per-authenticated-principal budget for index/detail/evidence GETs. Research setting 45 requests per principal per minute; quota exhaustion is 429 and quota-store failure is 503.
- PR #147, based on the separate #122 branch: index-only admission, 12 per socket peer per minute plus 60 shared index requests per minute, and 429 on quota exhaustion or quota-store failure. It has no independent in-flight concurrency fence. Peer identity may group legitimate users behind a reverse proxy.

## Rehearsal and observations
- Isolated Git merge rehearsal: draft #150 at 0f846182 and draft #147 at d00843fd.
- One textual conflict arose in tests/test_trace_investigation_ui.cjs. The Python route auto-merged, but would have applied both admission mechanisms to the same index request. This is a semantic conflict.
- A new AST regression guard, tests/test_trace_admission_policy_guard.py, passed 2/2 on the unmerged #150 branch. On the uncommitted combined route it detected duplicate admission (one expected failure, one passing test). The merge was aborted and the worktree returned clean.
- Neo4j evidence already exists in #122: isolated 5.26.30 benchmark on 85,000 synthetic groups and 170,000 events, count-query medians 181/222/259 ms and page-query medians 413/412/302 ms across failed/completed/open. Read-only profiles reported about 0.986 to 1.802 million database hits depending on query. This is not a sustained production p95/p99 proof.
- An independent Neo4j 5.26.26 startup attempt in this session did not reach query readiness; the disposable container was removed. No production Neo4j was accessed.

## Required decision
1. Reconcile one default-disabled admission contract before merging the parallel drafts. Do not silently layer both quotas.
2. Authenticate the operator first. P0 #149 requires verifying trusted-proxy identity provenance and stripping of caller-supplied headers.
3. Provide a global expensive-index budget and separate detail/evidence safeguards. Define fairness for per-user and per-socket-peer limits.
4. Add an independently fenced in-flight concurrency lease (issue #148). A per-minute count does not stop a concurrent burst.
5. Distinguish genuine exhaustion (429) from unavailable shared admission state (503), with documented Retry-After and fail-closed behavior.
6. Require real staging query plans, bounded concurrency, testable rollback, accessibility acceptance, and explicit operator authorization.

Release gates: Objective #137, authenticated browser/query issue #123, in-flight/proxy issue #148, trusted-header provenance issue #149, and baseline CI issue #143 remain open. Provider Usage & burn must stay staged. No source in this research grants command dispatch, provider-budget admission, executor attestation, NAS writes, or production rollout.
