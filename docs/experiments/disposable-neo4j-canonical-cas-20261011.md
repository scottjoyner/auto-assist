# Physical CAS, replay and rollback acceptance — disposable Neo4j 5.23, 2026-10-11

**Research-only:** x1-370 ran one isolated, memory-bounded Neo4j Docker container and removed it after testing. **NO production Neo4j schema, leases, providers, process, operator approval or model routing changed.**

This is the physical transaction acceptance for [draft PR #305](https://github.com/scottjoyner/auto-assist/pull/305), tracking [P0 issue #304](https://github.com/scottjoyner/auto-assist/issues/304), and **is not approval to merge or deploy**.

## Method and custody

- Exact source under test: commit `5e524ec4829c7a7cfb85a0cb0405b46f075ef235` for the strengthened concurrency run; opt-in code/tests at `scripts/run_disposable_runtime_approval_tests.sh` and `tests/test_runtime_projection_real_neo4j.py`.
- Physical runner: x1-370, locally cached `neo4j:5.23.0` **community** image; Docker container `assistx-cas-sandbox-...`; randomized **loopback-only** Bolt port **32788**. Docker `--memory 1700m --memory-swap 1700m --cpus 1.25 --pids-limit 180`; no persistent volume, no public port, no external model downloads, no NAS mount.
- Runner requires `ASSISTX_DISPOSABLE_NEO4J_TEST_ACK=run-one-local-ephemeral-db`, and the test suite additionally requires `ASSISTX_TEST_DISPOSABLE_ACK=ephemeral-neo4j-only`. Refuses normal Bolt port 7687, the previously used 17687, non-loopback hosts, existing graph nodes and missing test credentials.
- Temporary deterministic test signers were generated in memory for testing cryptographic behavior. They **are not operational source or operator authorities**.
- Disposable uniqueness constraints were explicitly created **inside that container only**: `FleetProjectionState.name`, `RuntimeProjectionApprovalReceipt.approval_id`.
- Automatic cleanup trap removed the Docker container. A follow-up read-only command verified no `assistx-cas-sandbox-*` containers remained, and the earlier random localhost port was closed. The repeated run also exited successfully under the same cleanup trap.

## Confirmed actual Neo4j outcomes

Six independently labeled physical integration tests PASS, including:

1. The two required real Neo4j uniqueness constraints are recognized by the verified schema evaluator.
2. A valid synthetic dual-signed canonical approval transaction actually commits a runtime, model, access paths, capacity, canonical generation and one approval replay receipt; exact source timestamps remain distinct from approval time.
3. Repeating the same generation/receipt fails; no second durable receipt appears.
4. New generation reusing the original approval ID fails.
5. Failure deliberately injected **after** several Cypher writes rolls the entire transaction back, preserving the previous generation, original admission, and original replay receipt.
6. **Eight independent two-worker simultaneous approval races**, each with competing approval IDs for the same expected generation: **exactly one committed per trial**; the other refused. Durable canonical generation and replay-receipt count remained exactly one.

The full real-database suite completed **6/6 tests PASS** and strict Ruff. The second, 8-race repeated run took approximately 5.3 seconds for its six unittest methods after container startup, and the disposable container was removed. These are real database transactions on *synthetic* manifests; they do **NOT** demonstrate a real AssistX signer, auth/owner binding, loaded physical model, real source telemetry provenance, or concurrent production traffic.

## Read-only production migration preflight

The actual x1 Neo4j database currently has **53 existing UNIQUENESS constraints**, but NEITHER needed identity constraint:

- Missing: `FleetProjectionState.name`
- Missing: `RuntimeProjectionApprovalReceipt.approval_id`

Read-only duplicate audit:
- `FleetProjectionState`: **1 total**, **0 null names**, **0 duplicate names**.
- `RuntimeProjectionApprovalReceipt`: **0 total**; the label itself is not yet present in production; **0 conflicting approval IDs**.

These findings reduce, but do not eliminate, schema-migration risk. They are **not consent** to run DDL. A designated DBA must approve migrations after backup, lock/contention review, rollback staging and proof of current writer compatibility. The draft `--apply` fails closed until both constraints exist, even with valid attestations.

## Additional remaining reader-side issue

Current `src/assistx/runtime_projection.py` checks stored `expires_at_ts` and canonical TTL. It does **not** uniformly enforce original `observed_at_ts` maximum age in the runtime/model/path/capacity eligibility path; runtime/model rows do not currently project those original timestamps into the builder. Thus a signed observation that was correctly collected earlier can still be treated as current by downstream readers for longer than its 5-minute physical / 3-minute path canary window, if the approval expiry lasts longer.

**DO NOT revert to stamping observation time with approval time.** Instead separately validate reader freshness from source timestamps, bound effective projection expiry to observation+window (as well as canonical expiry), and verify existing client compatibility, clock skew and physical path tests. This is a separate draft implementation/release gate.

## Operational NO-GO

No real signer custody/revocation, operator authority, source signing daemon, owner-bound receipts, production uniqueness constraints, reader observation-age enforcement, official new canonical approval, or fleet route promotion exists as a result of this experiment. The prior generation 643 remains expired. Real data always requires newly observed proof and authorized approval.
