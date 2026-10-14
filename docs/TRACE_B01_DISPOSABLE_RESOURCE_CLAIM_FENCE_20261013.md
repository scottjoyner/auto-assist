# B01 — transactional fixture Task-claim fencing (2026-10-13)

**Research-only. HOLD for production graph admission, claims, B01/B02/B06
cutover and automatic failover.** Stacked on draft PR #352, source
`7235ef20da00d2af7d3eba924b3eb7971dad2d6d`.
This adds **no production route, client integration, Compose change,
Neo4j data migration, network/credential modification, or deployment**.

## Fixed statement and transaction discipline

`tests/trace_b01_disposable_claim_fence.py` accepts only fixture Task IDs,
agent IDs, nonce, 64-character digest, integer term/sequence and boolean
recovery-identity witness. The independent fixture-only claim statement
matches `FixtureFence` and `FixtureTask` labels, never real `Task`.
It takes a write lock on the single scope guard before checking term,
exact next high-water sequence, unused nonce, pinned opaque digest,
READY status, owner identity and recovery-identity requirement.

A valid claim commits updated guard high-water/nonce history and
the Task claim in one explicit Neo4j transaction. A denial rolls back
**the entire transaction**, including the temporary guard-lock write.
No read-only policy classification by itself grants a graph write.
## Disposable physical findings

The opt-in `ASSISTX_B01_DISPOSABLE_CLAIM_FENCE=1` runner provisions an
ephemeral internal-only network and `neo4j:5.26-enterprise` container,
with no published graph ports, no bind mount or production attachment.
All writes are to new FixtureFence/FixtureTask nodes, deleted at cleanup.
Fixture owner, nonce and authorization digest are synthetic random values.

**Physical PASS (exit 0, 22.55 seconds observed command runtime):**
- Valid term/sequence/owner/recovery-confirmed claim applied exactly once.
- Replayed sequence, repeated nonce, already-claimed Task, wrong owner,
  incorrect digest and unverified recovery identity denied; whole graph
  state remained identical across the negative attempts.
- Old term denied after fixture-admin term turnover.
- Two simultaneous contenders for one next high-water sequence yielded
  exactly one successful claim.
- The restricted writer required pre-created property keys; the first
  physical attempt was rejected by Neo4j's CREATE NEW PROPERTY NAME
  privilege check. Fixture fields were precreated; the writer was not
  granted arbitrary property-name creation.
## Critical reproduced production NO-GOs

1. **Compromised writer credential bypass:** This fixture writer's
   permitted SET PROPERTY privilege allows direct Cypher that changes
   FixtureTask.status without incrementing high-water. A broker-side
   fixed Cypher plan is not a nonbypassable resource authority, even with
   narrow per-property grants.
2. **Graph-only history rewind:** A deliberately simulated admin restore
   rewinding guard.term/high_water/seen_nonces reaccepted a previously
   consumed claim nonce and sequence. An opaque digest equality check
   is not Ed25519 verification, independent high-water custody or replay
   protection after rollback. This did not restore a real filesystem,
   only mutate synthetic graph state to reproduce the failure condition.
3. **Authority spoofability:** The fixture executor accepts an asserted
   `recovery_identity_verified` boolean and an opaque digest. It does
   not authenticate an independent signed issuer or bind signed device,
   Task ownership, operation, term and sequence at Neo4j's authority
   boundary. No production credential/signer is validated.
4. **Limited scope:** This proves only fixture CLAIM behavior. Existing
   production heartbeat/complete/update methods, duplicate completions,
   state recovery, admission capacity and split-brain are not fenced.
## Smallest independent next gate

- Pin immutable, independently provisioned trust roots at the *resource*
  that performs the actual write. Verify signatures over canonical
  operation, Task, principal, term, sequence, nonce and expiry, and reject
  compromised verifier credentials carrying arbitrary digests.
- Store replay/high-water protection outside any graph-only restore
  domain, with monotonic rollback detection and atomic linkage to
  committed graph changes. Test crash between high-water reservation
  and physical write and ensure uncertainty fails closed.
- Do not expose a writer credential capable of arbitrary Cypher to
  API, worker or any of the seven graph-reachable production peers.
  A broker process compromise or privileged writer still requires a
  separate authority and cannot be considered closed by this proof.
- Prove all needed real graph consumers and policy/identity semantics in
  actual service images with a disposable full-stack, including complete,
  heartbeat, and idempotent retries. Then independently review network
  and host/Tailnet bypass denial and documented rollback.
- Independently review PR exact-head CI/physical tests. No merge,
  production mutation or failover promotion without separate approval.

**Disposition: SYNTHETIC TRANSACTIONAL PROGRESS, PRODUCTION NO-GO.**