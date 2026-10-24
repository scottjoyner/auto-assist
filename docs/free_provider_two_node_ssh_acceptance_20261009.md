# Synthetic free-provider shared lease: two physical nodes

**Date:** 2026-10-09, approximately 15:49 EDT.
**State:** Research-only. Not a production entitlement, signing witness,
distributed failover mechanism, or permission to run free-provider bursts.
**Related:** knowledge #55 / #57, auto-assist draft PR #229.

## Intent and boundaries

Use October 7 offline pilot commit 648683e4 to test ONE surviving authority
under physical cross-node contention without deploying any service. Both
x1-370 and xwing use existing SSH transport and Python 3.12; both submit
to a single disposable transactional SQLite file owned by x1-370 under
private /tmp/assistx-free-two-node-*.

One fixture upstream group maps two synthetic aliases. This is NOT evidence
of a real physical upstream quota. Capacity=1 slot, 10 requests/window,
90-second TTL, 100 input / 30 output reserved tokens. No model invocation.
No live router, database, daemons, credentials, or NAS state was changed.

## Observed evidence

1. x1-370 local and xwing-originated requests (xwing SSH to x1-370)
   contended for one SQLite authority: ONE GRANTED (x1-370, new_lease),
   ONE DENIED (xwing, slot_full); overlap=0; provider_calls=0.
2. x1-370 released the winner and got released=true.
3. xwing made a fresh remote request against the same authority,
   was granted, then released its lease.
4. The prototype's local audit hash chain verified; final active=0.
5. The PR #229 adapter separately passed 69/69 offline focused tests
   after release-acknowledgment hardening. Missing, malformed, or
   unavailable release acknowledgment now downgrades mock success
   and quarantines the synthetic quota group.

Note: the physical test executes the earlier SQLite ledger directly over
SSH. It does not wire the PR #229 mock adapter to remote RPC. Local ledger
hash verification is NOT an independent external audit witness.

## Negative cases not completed

- Remote copied-state reproduction was BLOCKED by execution safety
  controls before running. No new split-brain result is claimed.
  Previous separate admission research has reproduced a copied-state
  counterexample; that evidence is distinct from this session.
- Lost-authority failover, independent pinned host identity,
  application-level authentication or mTLS, key rotation, leader fencing,
  cancellation of live inference, and off-host archival remain UNPROVEN.
- Exact model $0 catalog data does not establish independent real quotas
  or remaining daily/monthly credits. No live OpenRouter, Z.AI, Cohere,
  Kilo, or OpenCode generation calls were made.

## Gate

Production activation remains HOLD. An issuer must have independent
authentication, single-writer authority with epochs and fencing,
quota-account/upstream ownership evidence, reproducible fault injection,
and independently witnessed audit custody. Never treat a copied SQLite
state as authority. Keep PR #229 draft; keep wider CI failures open under
auto-assist issue #145.
