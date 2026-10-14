# B01 — read-only diagnostic of remote witness and Neo4j effect evidence

**Date:** 2026-10-14. **Scope:** synthetic/disposable fixtures only. **Decision:** HOLD, no production promotion. Stacked on [Auto-Assist draft PR #363](https://github.com/scottjoyner/auto-assist/pull/363) pinned at `599bc8caf068c89ca25dc32054fc0d27a034df4c`. This adds **no production code, route, network/credentials, graph procedure install, release or failover mutation**.

## Why this gate is necessary

A graph-native signed claim (PR #362) and an independently signed witness reservation (PR #363) are stronger than client-side checks, but PR #363 physically reproduces two distinct no-go outcomes: after a witness timeout, graph has **no effect while witness is PENDING**, and after coherent graph+witness restore, an old **valid signed proof is accepted again**. Both are inconsistent with automatic retries, automatic pending clearance or unreviewed failover.

The Context three-host signed-certificate experiment separately observed that a follower still committed old-term effects after the primary advanced term (see [Context production P0 #31](https://github.com/scottjoyner/kipnerter-api/issues/31)); Context same-key xwing rollback showed that a valid signed preflight read can be followed by a database restore **before the actual effect**. Consequently, a signed status check or two cooperating witnesses does not grant mutation authority unless that constraint is enforced atomically at **every** effect resource. These are separate research findings, not proof that either system is integrated with AssistX.

## Implementation

`tests/trace_b01_readonly_effect_reconcile.py` is a pure, side-effect-free classifier. It consumes *untrusted synthetic* `WitnessObservation` and `GraphObservation` values; checks strict types, fixture identity/instance, term, high-water, pending/claim nonce and sequence. It emits an explicit operator diagnostic but **always** returns:

- `production_decision=HOLD`
- `automatic_reexecute_authorized=false`
- `automatic_pending_clear_authorized=false`
- `failover_authorized=false`
- `production_admission_authorized=false`
- `independent_postcommit_receipt_proven=false`

In particular, equal Task/agent/term/high-water/nonce observations are only `MATCHING_UNATTESTED_SNAPSHOTS`: the two source stores are mutable, and a matching pair cannot distinguish a genuine initial/committed state from coordinated restoration. An apparent empty pair (`high_water=0`, READY, no pending) is **`EMPTY_OR_COHERENTLY_RESTORED_INDETERMINATE`**, not a fresh-state grant. A separately held optional numeric floor can flag regression, but is **not** a transactionally enforced or independently signed monotonic authority, and never authorizes writes.

For observable fixture provenance, the off-graph witness additionally persists the canonical signer-validated `task_id`, `agent_id` and `nonce` in the already-durable PENDING record. The physical runner reads only synthetic Task/Fence fields and this compact witness state; no signing key, bearer or raw proof is emitted in the diagnostic. The legacy research Neo4j Task does not separately persist a post-COMMIT signed claim term, so the observer uses the guard's observed term as a **non-authoritative surrogate**; it must not be mistaken for independently signed commit evidence. No value from this diagnostic can be passed as a production approval.

## Actual physical acceptance — x1-370, fresh disposable fixtures

The existing opt-in PR #363 runner was extended with *read-only observations* after each material event; no new operational endpoints were introduced. Three fresh, separately executed runs with the cached `neo4j:5.26-enterprise` JDK image and isolated Python witness all exited **0**:

| Disposable case | Read-only observation | Independent graph/witness finding |
|---|---|---|
| `normal` | `MATCHING_UNATTESTED_SNAPSHOTS`, then `GRAPH_REWIND_OR_UNCERTAIN_UNCOMMITTED_EFFECT` | A legitimate dual-signed claim mutates FixtureTask, PENDING survives witness restart, and graph-only rollback cannot replay it through the intact witness. |
| `outage` | `GRAPH_REWIND_OR_UNCERTAIN_UNCOMMITTED_EFFECT` | Witness can reserve late after Neo4j times out; graph remains READY/old head, witness remains PENDING; **never auto-retry**. |
| `co_restore` | `EMPTY_OR_COHERENTLY_RESTORED_INDETERMINATE` | **Before** the readmission, both graph and witness look fresh after coordinated rollback. The same old legitimate signed proof then commits again: **critical NO-GO physically reproduced**. |

All tests are to **synthetic FixtureTask/FixtureFence and temporary ledger files**. The runner uses unique private internal Docker networks, no published graph ports or production graph mounts, source-compiled ephemeral JAR, synthetic private keys, and cleanup. No live assistx, graph, credential or network changes are authorized by this experiment.

## Independent next acceptance (still open)

1. Secure truly non-restorable monotonic custody with independently provisioned hardware/quorum/WORM-like trust, separate administrator domains and pinning rules. **No persistent TPM/NV or real hardware/keys were touched here.** Prove that coordinated graph/ledger/anchor restore under the same genuine trust root rejects replay **at effect time**, including stolen ordinary verifier credentials.
2. Independently attested *post-graph-COMMIT* evidence and an explicit terminality protocol distinguishing no effect, committed effect, side effect visible, and uncertain in-flight. No release or retry after a timeout until source-authoritative reconciliation.
3. Validate stale-effect rejection for **every** actual graph writer/heartbeat/complete path, not only synthetic claim; remove approved direct Bolt routes after real service-image parity and rollback review.
4. Independently review Java elevated procedure authorization, timing, bearer/channel confidentiality, root custody, replay retention, witness availability, clock/partition, and append-only audit. Complete exact GitHub-head CI/physical readback separately.

**No merge, deploy, native plugin install, production effect, automatic admission, claim reexecution, pending clear, failover, graph/credential/network or TPM mutation. B01/B02/B06 HOLD.**