# P0 machine identity cannot use legacy fleet worker Tasks

**Draft research / production HOLD.** Stacked on [PR #550](https://github.com/scottjoyner/auto-assist/pull/550) at `ca8faacc9b79ef173980e3e05c71cb955c8458a4`.

The deployed five node agents previously presented per-node credentials to Task API and received HTTP 401. PR #548 added a machine-token authentication path but did not supply actual, independent machine Task authorization. PR #550 separately denies machine-token calls at the five legacy Task endpoints, hardens credentialed HTTP and provides a strictly identity-only probe.

**Remaining source gap addressed here:** Even though PR #550's API refuses machine tokens, the worker's ordinary `_poll_once` and `_claim_and_run` still attached a machine token to legacy Task URLs. Operators could configure Basic credentials alongside a machine token and induce unnecessary secret-bearing HTTP requests, a 401 loop or attempted Task claims after an incomplete authorization cutover. The transport denied redirects/proxies but **did not authorize dispatch or bind destination to the separately approved identity-probe origin**.

## Narrow safeguards

- If `FLEET_NODE_TOKEN` **or** `FLEET_NODE_IDENTITY_APPROVED_ORIGIN` is set, the legacy `_poll_once` immediately returns no-work with **zero HTTP requests**. It does not switch to operator Basic, even if Basic is available.
- The direct `_claim_and_run` helper checks the same hold **before constructing session/claim/heartbeat or touching a model**.
- Starting the continuous `run` loop in this mode produces an explicit fail-closed error **before Router node-report, any model capability probe, Task poll or model work**. The controlled `--identity-probe-only` flow from #550 remains available.
- Without machine identity configuration, legacy source behavior remains unchanged; **this is source compatibility, NOT production deployment permission**. Existing general-purpose Basic credentials must never be distributed as a workaround for the five 401s.
- Pure-stdlib/socketless regressions cover valid token with Basic, partial machine identity, origin pin without token, direct claim helper, no worker loop, and old unconfigured HTTP 401 fallback.

## Acceptance beyond this patch

Machine Task execution requires separately registered/revoked scoped principals, operator-approved signed physical projection with positive slots, source-owned Task eligibility, generation/lease/claim/session CAS, exact model residency, independent external resource-accepted receipt and nonrestorable spent authority. Prove cross-node, replay, token rotation, stale claim and zero-slot negatives in disposable Neo4j and one supervised physical node before production. Do not infer that Task identity success or synthetic CI proves fleet workload readiness.

**No live Task claim, token, graph mutation, model, API, machine config, service, router, Docker image, staging node or production release was changed.**
