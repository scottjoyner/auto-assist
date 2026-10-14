# Auto-Assist mobile runtime and admission — release documentation checkpoint

**As of 2026-10-14 EDT:** documentation-only; **P0 HOLD / no deployment approval**. This document distinguishes research PRs from deployed behavior. Source code and verified live state outrank this note.

## Intended mobile integration

The Kipnerter iOS app obtains an authenticated mobile runtime catalog from AssistX. HTTP 200, a provider listing, an observed healthy provider, a resident native model, a fresh *admitted* runtime, actual execution and user-visible delivery are **different properties**. No implicit Agent Auto or model fallback may be inferred from any single status.

[Auto-Assist draft #361](https://github.com/scottjoyner/auto-assist/pull/361) and [integration draft #365](https://github.com/scottjoyner/auto-assist/pull/365) bind deny-only routing metadata to already admitted, lease-bound exact node/model matches; diagnostic graph or benchmark rows cannot mint a provider or grant runtime/agent/code privileges. Missing policy defaults to observer-only. A failed policy read rejects the whole projection. [Mobile error compatibility #358](https://github.com/scottjoyner/auto-assist/pull/358) addresses fail-closed blocked/expired projection errors.

System One receipt negative tests in [#364](https://github.com/scottjoyner/auto-assist/pull/364) exercise the real request_policy_shadow boundary; they do not demonstrate an independently wired persistent graph observer writer. Do not document the absent observer hook as deployed.

## P0 boundaries

**Credential custody:** [#156](https://github.com/scottjoyner/auto-assist/issues/156) requires operator assessment of historically tracked sensitive configuration and any necessary supervised rotation. A Git cleanup does not erase history. [Deployed-tree reconciliation #338](https://github.com/scottjoyner/auto-assist/pull/338) does not authorize redeploy.

**Shared router admin transport:** [#299](https://github.com/scottjoyner/auto-assist/issues/299) records multi-service token transport and migration/peer-identity concerns. Private-network plain HTTP does not establish confidentiality or a verified peer.

**Graph writer isolation:** [#294](https://github.com/scottjoyner/auto-assist/pull/294) reports direct production API/worker Neo4j network reachability. Research on a sole broker or Neo4j procedure does not prove the real direct Bolt-capable container paths are fenced.

**Rollback and uncertain commit:** disposable graph/witness tests in [#363](https://github.com/scottjoyner/auto-assist/pull/363) and [#366](https://github.com/scottjoyner/auto-assist/pull/366) reproduce a PENDING witness without committed effect and valid claim replay after coordinated graph+witness restore. Aligned mutable snapshots do not prove independently governed monotonic COMMIT. No automatic retry, PENDING clear, failover, or production admission is authorized.

## Test/authority distinction

| Reported draft evidence | Supports | Does not prove |
|---|---|---|
| #365 56/56 isolated source checks | deny-only routing and fail-closed shadow-request contract | deployed behavior, exact-head hosted full CI, independent graph authority |
| #366 299 focused source checks + three disposable physical cases | research classifier and co-restore counterexample | immutable high-water, trusted real COMMIT, safe effect recovery |
| #338 837 local tests and ancestry audit | candidate reconciled code lineage | credential rotation, versioned live tree, production redeploy |

## Review before release notes

Pin code, PR head, deployed process revision and test-SHA **separately**. Inspect actual mobile routes, catalog schema/expiry/error states, policy read failures and graph/no-failover boundaries. Record missing evidence NOT VERIFIED, not PASS. Keep all production graph, token, network, deployment, renewal, provider and release actions on HOLD; cross-check with [Auto-Router native owner #37](https://github.com/scottjoyner/auto-router/issues/37) and [iOS RC0 #395](https://github.com/scottjoyner/kipnerter-ios/pull/395).
