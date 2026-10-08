# Objective OBS-RC1 — Trace history, context evidence, and truthful provider burn-down

**Decision record:** 2026-10-08 EDT. **Status:** research integration / draft, **NOT** production release authorization.
**Purpose:** consolidate the fractured AssistX observability work into one evidence-backed *release objective*, while retaining independently reviewable changes and authority boundaries. This document describes an isolated integration candidate; it is not a claim that the running fleet has these changes.

## Baseline and ownership of each claim

| Slice | Source | Functional scope | Evidence | Gate |
|---|---|---|---|---|
| Trace workbench | draft #121 | read-only `/traces`, pagination, deep-link, lazy payload | original 7 Node; corrective #131 below | deployed auth/browser pending |
| Global history filter | draft #122 | server-side indexed outcome count+page | 17 original Python, 11 Node; synthetic 85k fixture, guarded benchmark | Neo4j 5.26 p95/p99, real query plans + 4s budget pending |
| Recorded context | draft #124 | allowlisted top-level event properties, local context navigation | prior 32 Python, 15 Node | browser + auth projection pending |
| Task/registry evidence | draft #128 | explicit trace→task relationships, opt-in registry lookup; labels unverified | prior 48 Python, 20 Node | deployed endpoint auth/429, plan/latency pending |
| Trace recovery | draft #131 | focus retention; detail retry; clearing stale read responses | isolated 9 Node + 21 Chromium before integration | source conflicts forward-ported in RC; actual staging pending |
| Provider burn model | draft #134 | offline, quota-window/unit/identity-fenced projection | isolated 15 Node; integration adds a timestamp mismatch negative test | real authenticated provider-limit source absent, route staged |
| Attestation research | draft #129 / issue #125 | synthetic source-claim MAC prototype only | NOT RC1 evidence of execution | external trust root / replay / revocation / WORM independent |
| Retention/custody | issue #117 | full nontrimming tool-call history | NOT proven by router trace index | independent full-chain source→sealed spool→NAS restore proof |

## Integration reconciliation performed only in a disposable checkout

- Source worktree: an isolated `/tmp/assistx-rc-review-*` git clone on x1-370; no changes to running services, Neo4j, shared NAS worktree or live providers.
- Baseline branch: draft #128 `feature/assistx-trace-evidence-20261008`.
- Reconciled missing **10 commits of documentation / synthetic benchmark validation** from latest #122 into the #128 branch lineage via a clean local merge. Branch #124 was 9 commits ahead / 10 behind #122 at review time; this is a release-evidence lineage mismatch, **not evidence of 10 missing production features**.
- Merged independent #134 burn model cleanly.
- Porting #131 required resolving precisely two overlapping files (`static/js/traces.js`, `tests/test_trace_investigation_ui.cjs`). Kept global server-side filtering, context navigation, opt-in task evidence, old-backend denial, unknown metric semantics, and focused-row restoration.
- Integrated corrections: index failures invalidate in-memory `currentTrace`, `currentEvidence`, all pending detail/evidence responses and cached selection. Auth-expired totals are *unknown (`—`)*, not fabricated zeros. Added regression for late evidence response after 401.
- Added burn contract negative: latest historical usage sample must have the *same timestamp* as the meter snapshot to forecast exhaustion; the same numeric counter from an older sample is not current evidence. Historical burn observations never grant quota authority by themselves.
- Added reproducible read-only GitHub Actions workflow (Node contracts, synthetic Python query fixtures, loopback-routed Chromium). Workflows make a synthetic gate repeatable; they do not establish real auth or deployed query performance.

## Review and disposition — what should have been done better

| ID | Severity | Observation / improvement | Disposition | Exit evidence |
|---|---|---|---|---|
| D01 | P0 | Initial JS VM tests did not exercise actual focus after `innerHTML` replacement; browser found it. | **Fixed and integration-tested** (#131 port). Make Chromium keyboard test a blocking CI job. | Tab/Enter/Shift+Tab, focus ring; authenticated screen-reader/manual check still open. |
| D02 | P0 | PRs were green individually, but their branch bases diverged and the latest validation was not included in later slices. | **Integrated in research clone**; reconcile the real PR chain before promotion; do not force-push others' branches. | Compare all ancestors and exact SHAs, clean three-way merge, CI for combined head. |
| D03 | P0 | Recovery PR #131 targeted #121 and did not cover filtered context/evidence UI, creating two source conflicts. | **Forward-ported in RC** with preserved server filter/context/evidence and 23 Node regressions. | Integrated browser + API fixture, peer review of conflict resolution. |
| D04 | P0 | Index failure cleared visible detail, but later stack could retain in-memory context/evidence and late evidence responses. | **Corrected in RC**: generation bump, memory invalidation, negative pending-evidence/401 regression. | 401/403 + delayed GET denial; no exposed or reappearing context data; verified on real auth later. |
| D05 | P0 | Offline burn model labels a *caller-declared* flag `authoritative`; this is NOT authenticated provider quota custody. | **Blocked for UI**; never display trusted credits/remaining or use for admission until upstream identity, quota authority and units are proven. | Independent provider-specific provenance and time-window source audit, credentialless negative tests. |
| D06 | P1 | Burn slope could use sample counter=latest number from an older timestamp and pretend it was current. | **Fixed in RC** with timestamp equality requirement and negative fixture. | Matched `observed_at` and monotone sequence required before forecasting. |
| D07 | P0 | Missing provider Usage & burn routes previously returned 404, although dashboard navigation wanted to advertise them. | **Staged**, never silently enable. Read-only projection must handle 200/401/403/404/429/503 and unknown units. | Authenticated 200 on both approved deployed routes and documented rollback, then separate UI change. |
| D08 | P0 | Mock HTTP fixture is not deployed middleware: index, detail and evidence 401/403/429/rate-limit behaviors remain unproven. | **Release blocker**. | Non-admin/expired credentials, session renewal, live limits and audit response proof on operator-approved staging. |
| D09 | P0 | Neo4j 85k synthetic timing included container/Java startup; not valid driver p95/p99/DB hits, no 5.26 staging plan. | **Release blocker** for global filter/evidence endpoint. | Read-only EXPLAIN/driver timings with concurrency/4s transaction timeout, indexes and production-equivalent staging resource budget. |
| D10 | P1 | Browser accessibility evidence was late and over-relied on VM/axe; touch and screen-reader behaviors still unchecked. | **Blocked for release**; integrated synthetic keyboard/context/filter/evidence checks are additions, not a substitute. | Real 375/tablet/desktop authenticated run, screen reader, WCAG contrast, deep-link and keyboard audit. |
| D11 | P1 | Local successes were reported without source hashes, CI checks or shared artifacts; some negative failures came from fixture sequencing. | **Addressed partly** through reproducible suites, exact tested tree and one read-only workflow. Keep CI status distinct from local passes. | GitHub Actions reports for exact RC SHA, documented test versions, failure lineage and replay command. |
| D12 | P0 | Registry `node_id` matches and `source` labels can be mistaken for authenticated execution or custody. | **Never promote labels into 'verified executor'.** Keep attestation #125 and full fidelity #117 independent. | External trust root, replay/revocation and independent witness acceptance; no machine verified badge beforehand. |
| D13 | P1 | Release slices mixed backend query risk, frontend presentation and provider-budget semantics into one subjective green signal. | **One objective, multiple independent gates**. Retain parent/child PR review order and an integrated validation branch. | Gate matrix signed off per layer, read-only rollback, explicit release authorization. |

## Observable acceptance criteria (RC1)

**Gate A — synthetic integration:** all focused Node trace+burn tests pass; Python fixed-query/provenance/bench-guard tests pass; Chromium responsive/keyboard/filtered-deep-link/context/evidence/401/429/503/disclosure tests pass. CI on exact RC branch reports success. **Synthetic gates do not authorize production.**

**Gate B — authenticated staging:** real deployed `/traces` and index/detail/evidence GET are denied anonymously and under insufficient scopes; operator session refresh/expiry produces no stale data; rate-limiter and 429 handling verified; no non-GET requests or automatic registry inspection. Preserve provenance and ambiguity warnings.

**Gate C — query cost and resource safety:** staging Neo4j 5.26 `EXPLAIN` and bounded driver p50/p95/p99; verify count/pagination predicate consistency, 4s timeout and at-most-12 evidence tasks; no benchmark against production/NAS without a preapproved I/O limit.

**Gate D — provider burn authority:** independent verified meter adapter with source provenance and timestamps, declared units/window/reset/limits, explicit unknown and stale states, no API secret exposure. No claim that local token counts equal provider quota. Both Usage routes remain staged/404 until authenticated and deployed separately. No quota admission logic in UI.

**Gate E — accessibility and rollback:** human keyboard/touch/screen-reader validation at 375px/tablet/desktop, no sensitive data in URL/clipboard/screenshots/logs; demonstrate rollback of static assets/API query handler. Obtain explicit operator approval for deployment.

**Independent non-goals:** No full historical tool-call retention acceptance (#117), executor attestation (#125), Mercury integration, lease issuance/revocation, real provider free-budget admission, agent dispatch, service restart, NAS migration or Neo4j production mutation.

## Promotion order and ownership

1. Confirm RC integration branch includes exact latest #121 → #122 → #124 → #128 plus #131 fix and #134 projection; review the two manually resolved files and the benchmark-guard merge. Keep source PRs open/draft until their own acceptance is complete.
2. Make Gate A required on a pinned commit, upload scrubbed synthetic evidence. A green isolated test is only **evidence**.
3. Ask a staging operator to conduct Gates B and C under an explicit read-only resource budget; document captures and abort limits. Stop on denial failure, timeout or privacy leakage.
4. Perform Gate D separately before any live Usage & burn route; otherwise the RC may ship trace-only with the burn tab disabled.
5. Gate E signs the rollout/rollback record and requires an explicit deployment decision. No blanket release from PR mergeability alone.

**Rollback:** discard/close research integration branch; retain original draft PRs and their commit histories. For a later approved deployment, revert static/template and query changes independently using recorded SHAs; do not touch archived traces, NAS or provider accounts.

**Decision:** proceed with a *single reconciled objective and synthetic integration*, **no production activation**. Current synthetic passes do not close Gates B–E or independent #117/#125.
