# Signed canonical runtime projection: original-source age enforcement (2026-10-11)

**Draft-only, client/reader compatibility slice stacked on [signed canonical apply PR #305](https://github.com/scottjoyner/auto-assist/pull/305). No production read/write, signer, model load, runtime admission or routing configuration was changed.**

## Correctness gap

The signed approval transaction now preserves separately verified physical/model/capacity/path `observed_at_ts` instead of stamping the newer approval time. However, the pre-existing read side in `src/assistx/runtime_projection.py` only checked canonical and per-record `expires_at_ts` when determining whether a runtime could be projected. With a 900-second approved TTL, a 3-minute-old private path canary could remain apparently ready until the entire approval expired, even if the original path proof was far older than its permitted age. Merely preserving observation timestamps in the graph **does not make readers enforce them**.

## Narrow compatibility-preserving fix

The signed apply PR records `FleetProjectionState.source_receipt_sha256` from the actual independently verified source signature at approval time. This separate *reader* PR consumes the canonical state's digest strictly as a marker that the **signed source enforcement protocol** applied. It is not a user-supplied signature and never grants admission or dispatch.

For canonical states with that valid 64-character lowercase SHA256:
- Retrieve original `RuntimeInstance.observed_at_ts` and `LoadedModelInstance.observed_at_ts` in the graph read projection.
- Require original runtime, LOADED model and capacity observation ages **≤300 seconds**, with a 5-second maximum future skew and integer-only timestamps.
- Require original approved private path canary observation age **≤180 seconds** for each projected path.
- Cap the final signed projection lease to **the minimum** of requested TTL, canonical approval expiry, every physical observation timestamp+300 seconds, every path timestamp+180 seconds, and the underlying per-row `expires_at_ts`. A later approval timestamp **cannot extend a stale observation**.
- Refuse missing, false, future-dated or expired source-observation timestamps. Do not silently drop an expired *observed* private path and claim an equivalent fresh path; surface an explicit blocked condition.
- Preserve legacy unsigned schema-1 reader behavior **only for the migration window**, because existing fixture and caller compatibility depends on it. A legacy state with no source receipt is **NOT source-authenticated** and must be separately disabled/retired after the staged client and operator rollout. Never promote a legacy state merely by adding an arbitrary digest property outside the independently authorized writer.

Neither the source receipt marker nor an HTTP 200 means a physical model is loaded or an owner is admitted. The source writer must be independently validated, and an atomic current admission still belongs to a separate authority.

## Local isolated verification

Exact-source tests:
- **11 new** standard-library + FastAPI source-only tests in `tests/test_runtime_projection_source_age_reader.py`.
- **7 existing** tests in `tests/test_runtime_projection.py` and `tests/test_runtime_projection_v2.py` still pass with unchanged legacy fixtures.
- Disposable x1-370 source checkout: `18 passed in 0.21s`, strict Ruff clean, at commit `84d45ffe38f20d1d5b7f138c39fc48bc2df77cde`.
- The new tests prove that a signed approval with a canonical TTL of 900 seconds but private canaries originally sampled 60 seconds ago returns a lease ending **120 seconds** from now, not 900 seconds. A new projection is refused 121 seconds later unless a genuinely fresh path observation is signed/approved.
- Missing physical/runtime/model/path/capacity timestamps and future-dated canaries reject the entire signed read. Legacy fixtures remain independently recognized as *legacy*, not converted into signed witnesses.

## Remaining release NO-GO

1. This read-side PR is stacked on draft #305 and cannot be deployed before the independent signer, verified source-producer, schema migration, and operator-approval gates exist.
2. Retire or expressly restrict legacy unsigned canonical approvals after client compatibility and branch promotion; they do not gain security from the signed source-age checks.
3. Verify Auto-Router's cache replacement/expiry behavior when signed physical source TTL is shorter than the canonical approval TTL. Test exact generation, signed v2, failover to **blocked** on expiry, iPhone/Destroyer loaded state, and two-user owner-bound physical execution receipts (Auto-Router issue #37).
4. Repeated source observations require a **fresh independently authenticated approval** (or separately approved limited delegated authority); never auto-renew with old source signatures.
5. Existing broad AssistX CI source binding and recovery canary failures remain separate production gates, even when this focused suite is green.

**The production graph, existing canonical generation and fleet routing were not modified.**
