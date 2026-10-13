# P0 offline independently signed admission ledger — uncertain effects HOLD

Date: October 13, 2026. Isolated x1-370 source research only.
Stacks on draft [PR #344](https://github.com/scottjoyner/auto-assist/pull/344),
which itself stacks on the two-signer validator in draft PR #305.
**NO live Neo4j writes, credentials, routing, role, or service changes.**

## Boundary being tested

The running AssistX API/worker currently use Neo4j's builtin admin account,
which can bypass an application-level verifier. Draft PR #344 physically
proved a separate read-only API/worker role could be blocked from canonical
writes, but a compromised scoped broker credential could still submit an
unsigned digest and decrement generation. Unique constraints alone do not
prevent that. The existing signature verifier in PR #305 already enforces
`generation == expected_current_generation + 1` at manifest validation;
its flaw is not arithmetic but **exclusive authority over the write path**.

This research adds two source-only modules:
- `tests/runtime_projection_offline_admission_ledger.py`: validate PR #305's
  real Ed25519 source+operator signatures with externally injected pinned
  public roots, require exact next generation and ≤900s TTL, then atomically
  reserve one approval intent in a mode-0600 *disposable SQLite ledger*.
- `tests/test_runtime_projection_offline_admission_ledger.py`:
  ephemeral two-signer positive, invalid/stale/future/key swaps,
  malicious TTL, nonmonotonic generation, eight concurrent attempts,
  immutable initial genesis, access-permission and crash/HOLD tests.

**Intent remains PENDING_UNCERTAIN_GRAPH_OUTCOME indefinitely.**
There is intentionally NO Neo4j client, no broker graph-write token, no
automatic commit, no reset, no release and no production endpoint. A caller's
claim of commit is never accepted as an authenticated witness. If the process
crashes after the admission reservation, the next attempt stays HOLD.

## Safety and gaps

- The artificial `trusted_genesis_generation=6` fixture is NOT a defensible
  production bootstrap from the current mutable canonical 643. The governing
  authority must independently attest genesis and rollback-proof store
  identity before deployment.
- The validation clock and pinned keys are injected from the trusted caller
  in research. An untrusted API payload cannot set either.
- SQLite mode-0600 on one host does NOT withstand an owner/administrator
  who can replace the ledger file, nor offer multi-node distributed quorum.
- Reservation in SQLite and effect in Neo4j cannot atomically commit across
  both without a separately designed reconciliation protocol. Pending states
  are conservatively quarantined forever in this slice, not declared success.
- This does not mitigate unrotated production Neo4j admin credentials.
  The approved resource boundary must exclusively hold the write credential
  behind authenticated, fixed-plan, independent cryptographic verification.
- A human-approved recovery process needs independently signed graph commit
  evidence, replay/high-water fencing and explicit negative replays before
  any restart or retry can clear a pending record.

## Gate and disposition

Run `ASSISTX_P0_LEDGER_RESEARCH=1` only against a disposable temporary
SQLite path, with ephemeral test keys and the inherited PR #305
`runtime_projection_signed_apply_gate.validate`. Never put a signing
private key, live URI or token into the experiment.

The local suite must pass along with a GitHub-hosted **source-only** workflow.
Neither result authorizes application merge, new database constraints,
runtime admission, shared-admin rotation, graph cutover, reservation release
or automatic failover. **B01/B02/B06 remain HOLD.**
