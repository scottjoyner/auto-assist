# Auto-Router admin client-first migration — exact-head acceptance (October 11, 2026)

**Disposition: DRAFT / NO DEPLOY.** This handoff is a non-secret, read-only evidence reconciliation and never authorizes a production router, AssistX API, model, Neo4j, storage, Govee, or iPhone change. It specifically distinguishes GitHub integration acceptance from credential-file custody and physical-runtime admission.

## Exact repositories and evidence

| Dependency | Tested head | Evidence and boundary |
|---|---|---|
| AssistX client [PR #288](https://github.com/scottjoyner/auto-assist/pull/288) | `7aad4905910be965ad50b17a68ffa96373cf857e` | Authenticated sanitized `GET /admin/providers/effective`; old-router **404-only**, authenticated legacy fallback; no fallback on 401, 403, schema mismatch or 5xx. Draft, not deployed. |
| Auto-Router admin fix [PR #41](https://github.com/scottjoyner/auto-router/pull/41) | `b43c3b8565390b32e3fbbae5b557db08c29d4edf` | Authentication on inherited admin paths and sanitized effective in-process inventory; CI and admin read-only contract **PASS**. Draft, not deployed. |
| Existing green CI repair [AssistX PR #265](https://github.com/scottjoyner/auto-assist/pull/265) | `2208903fa029b179f835d749c0f4526684dd936d` | Separate disposable compose with PR #288: **791 passed, 59 deselected** and recovery-canary PASS. Its patch set includes tracked environment-example path changes; CI success is **not** a credential-custody authorization. |
| Newer stacked repairs [AssistX PR #291](https://github.com/scottjoyner/auto-assist/pull/291) | `41c37863dd1b0b03585f2c77221796fb6795bb18` | The PR #288 + #291 + [#290](https://github.com/scottjoyner/auto-assist/pull/290) **disposable** composition passed **861 unit**, **225 targeted**, **12 recovery** tests, 59 deselected in the full unit run. **A separate PR #291 custody check intentionally fails**: `HOLD_PUBLIC_ENV_DIFF` for three sensitive-path changes (one addition, two deletions). |
| Auto-Router signed projection authority [AssistX issue #284](https://github.com/scottjoyner/auto-assist/issues/284) | Current canonical **642** | Production signed projection is **expired; fresh=false**, AssistX projection source HTTP 503 with deliberate rejection. Actual `NEO4J_DATABASE` shows zero qualified/unexpired runtime, loaded-model, access-path or capacity witnesses. x1 LM Studio currently lists **zero** loaded models. Neither admin fix changes admission. |
| Fleet Model Link [AssistX PR #285](https://github.com/scottjoyner/auto-assist/pull/285) / [Fleet Hardware #27](https://github.com/scottjoyner/fleet-hardware/pull/27) | Draft | Physical Destroyer/Lenovo raw model servers still listen on `0.0.0.0`, respond directly from x1, and are **not** isolated or signed-approved. Operator canary remains disabled; iPhone acceptance absent. |

All synthetic CI results above were collected before this documentation commit. They show *candidate code compatibility*, not production acceptance. The PR #288 isolated head itself retains 77 baseline-style failures; do not call the standalone PR fully green. The PR #291 sensitive-path HOLD cannot be waived by a composed green test run.

## Confirmed live security exposure and legitimate consumer

From Destroyer and Lenovo, **credential-free** GETs to x1-370's *private Tailnet* router address returned **200** for `/admin/providers`, `/admin/circuits`, and `/admin/agent-workers`, and **401** for independently protected `/admin/runtime-projection`. No remote response bodies were collected. This demonstrates a private Tailnet admin-auth bypass, **not** public-WAN reachability or confirmed credential disclosure. The sanitized evidence is in [Auto-Router PR #41](https://github.com/scottjoyner/auto-router/blob/security/effective-provider-snapshot-20261010/docs/evidence/router-admin-tailnet-auth-20261011.json).

Conversely, the running `assistx-api` container has an **existing** `AUTO_ROUTER_ADMIN_TOKEN`; using it for a bounded GET to the currently protected `/admin/runtime-projection` returned **200**. AssistX `_provider_catalog` passes the same configured token to privileged catalog requests. This checks **one legitimate caller**, not every browser/operator/CLI consumer. Never expose the token in URLs, JS, screenshots, git, logs, or task traces.

## Minimal supervised rollout sequence (not executed)

1. **Owner/security approval and custody:** audit the `HOLD_PUBLIC_ENV_DIFF` before merging any repair stack containing sensitive example-path changes. Treat additions and deletions equally as material until safe-public diff custody is confirmed. Do not restore retired env templates just to make a test pass, and do not disable the custody gate.
2. **Client first:** review and deploy only an approved AssistX client composition preserving `AUTO_ROUTER_ADMIN_TOKEN` custody. With the current old router, new effective `GET` should return **404**, followed by a single **authenticated** legacy GET **200**. A 401/403/5xx/malformed response must **not** fall back. Check the Fleet panel retains `AVAILABLE` / `CONFIGURED` separately from graph-backed **`LOADED`** and that its model-list response never grants capacity.
3. **Audit the remaining admin clients:** the AssistX Control Room helper is known to supply an admin token; catalog consumers, old scripts and non-clickable browser admin links need exact caller-by-caller checks. A normal browser navigation cannot add `X-Admin-Token`; never invent a browser-side token workaround.
4. **Router patch next:** after client acceptance, deploy reviewed Auto-Router PR #41 under an approved rollback window, changing only the admin-auth/sanitized-provider layer. Confirm from two Tailnet peers that anonymous `/admin/providers`, `/admin/circuits`, `/admin/agent-workers`, and `/admin/runtime-projection` all answer **401**, and the authenticated `/admin/providers/effective` returns schema 1 with `kind=EFFECTIVE_ROUTER_MEMORY_READ_ONLY`, sanitized addresses and no secrets. Confirm the existing authorized AssistX caller still gets **200**.
5. **Negative-case gate:** no credential must leave its configured origin on a 30x redirect; no anonymous legacy fallback when a privileged call is denied. Verify no changes to selected provider/model IDs, signed projection generation, inference stream or persisted routing receipts as a side effect.
6. **Separate admission and physical recovery:** only the rightful owner can issue fresh x1 runtime/model/path/capacity witnesses and a newly signed, CAS-bound next projection; **do not refresh generation 642's expiry**. Preserve the foreign-revision renewal HOLD. Never substitute Destroyer K2 1B for a historical 36B alias. Require one-service-at-a-time raw-listener isolation with an observed consumer and rollback map before any Fleet Model Link or phone exposure.

## Outcome matrix

| Scenario | Expected behavior | Current evidence |
|---|---|---|
| Old router + reviewed new AssistX client | Authenticated effective 404 → authenticated legacy GET 200; UI says `AVAILABLE`, not `LOADED` without independent graph evidence | Synthetic coverage + pre-deployment old-router 404/authorized legacy 200; **client not deployed** |
| New router + new client | Authenticated schema-1 effective 200; legacy route remains protected | Source/CI only; **router not deployed** |
| New router + anonymous Tailnet client | All sensitive inherited admin GETs return 401 | **FAIL on running router:** three measured HTTP 200 |
| Missing/wrong admin credential | No provider-data retrieval and no anonymous downgrade | Tests pass; current inherited live GETs remain exposed |
| Runtime signed projection expired | Execution admission refuses request despite `/health` 200 | **Confirmed live fail-closed** |
| x1 historical Ornith key but zero loaded models | No authority to claim it resident or issue new projection | **Confirmed NO_GO** |
| PR #291 public sensitive-file diff not approved | No promotion despite green test composition | **Current HOLD_PUBLIC_ENV_DIFF** |
| Destroyer/Lenovo raw llama servers wildcard-bound | Operator canary + iPhone remain disabled | **Current HOLD** |

**Promotion decision: NO-GO until explicit owner approval, closed custody, and supervised client-first physical acceptance.** No service was stopped, started, reconfigured or deployed while preparing this document.

## October 11 dependency and four-service transport reconciliation

This section supersedes the older checked-head references above for the
**specific newly tested client composition**, without treating any draft
as a release artifact.

- [AssistX PR #288](https://github.com/scottjoyner/auto-assist/pull/288)
  was pinned at `72ce1bd9675d787cf909a6586516583359108bac`.
  [Three-way hosted run 38158970644](https://github.com/scottjoyner/auto-assist/actions/runs/38158970644)
  **PASS** for targeted, non-integration unit, and recovery-canary; the
  targeted suite now includes `tests/test_router_admin_transport_custody.py`.
  [Source-binding 38158970738](https://github.com/scottjoyner/auto-assist/actions/runs/38158970738)
  PASS; [repair-stack 38158970712](https://github.com/scottjoyner/auto-assist/actions/runs/38158970712)
  PASS. Standalone [CI 38158970746](https://github.com/scottjoyner/auto-assist/actions/runs/38158970746)
  still FAILS and is not waived.
- [Admin scripts PR #290](https://github.com/scottjoyner/auto-assist/pull/290)
  is pinned at `3a77e7d626b3a9f0047c69954ad94deadd30dcf0`.
  It adds the metadata-only `router_admin_transport_custody.py` contract,
  synthetic regressions, and
  [four-service transport review](https://github.com/scottjoyner/auto-assist/blob/fix/router-admin-script-clients-20261011/docs/security/router-admin-cross-service-custody-20261011.md).
- [Repair PR #291](https://github.com/scottjoyner/auto-assist/pull/291)
  remains pinned to `41c37863dd1b0b03585f2c77221796fb6795bb18`.
  Its public sensitive-path custody gate still intentionally returns
  `HOLD_PUBLIC_ENV_DIFF`. Separately, the historical-commit review in
  [draft #293](https://github.com/scottjoyner/auto-assist/pull/293)
  retains its fail-closed analysis of #291's public history.
- [Auto-Router #41](https://github.com/scottjoyner/auto-router/pull/41)
  has separately green hosted cutover-contract, full CI and undelivered
  image/restore testing at draft head `18412e242afdfd0149311bf0da4975d1339b1f4c`.
  The actual x1-370 production private Compose stack was later verified
  to accept a *synthetic*, non-loaded immutable-image override without
  modifying any other field. This is not a real candidate image staging
  receipt, nor router deployment.

**New P0 dependency: [AssistX #299](https://github.com/scottjoyner/auto-assist/issues/299).**
The running router, API, worker and Hermes adapter use a **shared admin
token**. The three consumer services reach configured router origins over
HTTP on private RFC1918 networks. Protected route GETs demonstrate
configured-auth reachability; they do **not** prove transport confidentiality
or authenticated router peer identity. A single shared token with no proven
two-token overlap means rotation requires an owner-approved simultaneous
consumer/router transition or a separately reviewed overlap mechanism.

Do not infer that the router's incidental Docker hostname works inside
worker/Hermes; their individually configured origins are the actual
acceptance targets. Do not join extra Docker networks or inject token into
a browser to bypass caller-audit failures.

**Security sequencing:** the currently deployed router's five anonymous
HTTP-200 admin GETs present a *separate live access-control exposure*.
An authorized security owner may choose urgent reversible ingress
containment ahead of the full release after reviewing legitimate callers;
do not postpone a containment decision merely because historical credential
custody #156 remains open. Equally, do not apply unsupervised network
restrictions that could cut off the three known consumers. A contained
network is not equivalent to completed token custody or router auth.

Release remains **DRAFT / NO-GO** until [#156](https://github.com/scottjoyner/auto-assist/issues/156)
and [#299](https://github.com/scottjoyner/auto-assist/issues/299) have
owner decisions, actual private Compose/image/rollback custody is approved,
clients work through their configured origin, and postcutover six-route
401 denial is independently witnessed from x1, Destroyer and Lenovo.
