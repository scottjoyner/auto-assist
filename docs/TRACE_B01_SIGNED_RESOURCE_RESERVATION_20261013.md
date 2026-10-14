# B01 signed resource-reservation research — October 13, 2026

**Disposition: PASS_SYNTHETIC_WITH_RESOURCE_NO_GO.** Draft research, not a
production graph writer, credential issuer, or grant to deploy/admit/failover.
Stacked on [draft PR #354](https://github.com/scottjoyner/auto-assist/pull/354)
at pinned source `d5d5a7652db80092feece6b8af7a000b38f9a593`.
No live Neo4j data, host port, network, credentials, service, or API changed.

## Threat being addressed

PR #354 proved that a guarded Cypher claim with an opaque digest permits
(1) arbitrary-Cypher writes by a compromised graph writer and (2) replay
of a consumed proof after graph-only high-water restoration. Related
Context evidence independently reported compromised verifier credentials
able to submit arbitrary digests to PostgreSQL without DB-side Ed25519
verification. A verifier's successful check is not a non-bypassable resource
capability if its receiving resource trusts the verifier's word.

## Smallest implemented isolated contract

`trace_b01_signed_reservation_gate.py` is an **out-of-graph resource
reservation facade**, not a Neo4j plugin. Two **different, pinned fixture
Ed25519 public roots** verify signatures over the **same canonically encoded
body** before durable reservation. The body binds fixture instance, scope,
operation, Task, agent, term, exact sequence, nonce, policy digest, issuance,
and <=60-second expiry. Caller-supplied bare proof digests and extra fields
have no admission path. After verification, all policy values are checked
against the separately initialized local authority record.

A fresh SQLite `BEGIN IMMEDIATE` transaction serializes reservations. It
requires term equality, exact `high_water+1`, unused nonce, no outstanding
PENDING reservation, and source-owned Task/agent/policy binding. Before
committing the ledger, the gate fsyncs a separately located checkpoint
file and directory. A crash between checkpoint and DB commit intentionally
leaves a mismatch and blocks reopening; DB-only rewind is detected. The
gate returns **PENDING_UNCERTAIN**, with `graph_write_authorized=false`.
There is deliberately no automatic close, retry, release, or failover path.

The fixture generates independent private keys at test time, does not store
those keys in the graph, and never exposes signatures/keys in its result.
Actual private-key separation, hardware root custody, external monotonic
counter, multi-host persistence, trusted clock, quorum consent, resource
in-flight reconciliation, and proof-of-physical-closure are **unproven**.

## Negative acceptance (synthetic)

- Valid dual-signed fixture reservation is durable and fails closed on
  immediate or later replay; concurrent same-sequence reservations admit
  exactly **one** pending entry.
- Invalid or swapped signatures, forged issuer/device signature, changed
  Task/agent/policy digest, unknown schema/operation/scope, unsigned supplied
  digest, expired/future/oversized TTL, stale/forked term or sequence fail.
- A mismatched trust root on reopening is rejected; the request object is
  frozen before verification to avoid mutable post-signature policy changes.
- DB-only restore fails closed against the independently stored checkpoint.
- Simulated power loss after fsynced checkpoint but before database commit
  fails closed, rather than automatically replaying the uncertain attempt.
- **Coordinated restore of both SQLite and checkpoint replays an old proof.**
  This intentionally reproduced negative means the checkpoint is **not** an
  independently trusted rollback-proof authority; it is only separate from
  a **graph-only** or **DB-only** restore domain.

## Disposable Neo4j physical acceptance

Opt-in `ASSISTX_B01_DUAL_SIGNATURE_PHYSICAL=1` provisions a uniquely named
`neo4j:5.26-enterprise` instance on an internal Docker bridge, without host
ports, bind mounts, persistent graph custody, real credentials, or live
service attachment. Two synthetic Ed25519 signers generate a fresh proof.
The separate SQLite/anchor facade verifies it and records pending state,
then the synthetic fixed `FixtureTask` Cypher claim executes under a narrow
Neo4j writer. A forged issuer signature and post-signing Task identity
change are rejected *before* the actual graph operation. On graph-only
rewind, the local signed gate refuses replay. DB-only restoration yields
checkpoint mismatch and denies reopening.

**Critical expected NO-GO remains reproduced:** with the leaked graph writer
credential, direct Cypher/`try_claim` still bypasses the gate after graph
rewind, despite both signatures being verified by the facade. The graph has
**not** validated either Ed25519 signature, and cannot distinguish an
unsigned bypass through a privileged direct connection. The live seven-peer
Neo4j TCP bypass evidence from PR #347 is therefore still relevant.

## Production blockers and the next research boundary

1. Enforce pinned trust roots and signature/replay validation at a genuinely
   non-bypassable **write resource authority**. A Python check followed by a
   separately credentialed unrestricted Neo4j writer is insufficient.
2. Move high-water state into independently secured monotonic/quorum custody
   that **cannot** be rolled back with graph and local checkpoint copies.
   Explicitly reconcile uncertain graph in-flight transactions before any
   reexecution or term takeover. Never silently advance or release.
3. Authenticate signer provenance and per-task owner/agent/operation
   authorization (including recovery identity), revocation and policy digest
   against source-authoritative evidence. The fixture keys, timestamp and
   caller-owned Task bindings are not real trust or identity.
4. Deny arbitrary Cypher using real graph grants/topology, validate service
   images, all Task/heartbeat/complete paths, host-published and Tailnet
   bypasses, and prove rollback under independent review and approval.
5. Repeat exact published-head CI, physical acceptance, provenance review,
   and negative fork/restore/credential-compromise tests. Do not substitute
   synthetic success for real admission/release authorization.

**B01/B02/B06 remain HOLD, all changes draft-only.**