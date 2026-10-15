# Three-witness signed hash-chain heads do not prove the latest durable tip

**October 15, 2026 | Fleet Commander x1-370 | DRAFT / CRITICAL NO-GO**

## Exact question

Would independent *cryptographic identities* — two out of three Ed25519
signatures on an adjacent, linked, fresh checkpoint — prevent an operator from
replaying an old signed high-water floor after restore?

**No**, not without independently enforced latest-tip durability, signer
administrative separation, and an authoritative COMMIT protocol. Hash chains
show the predecessor of a PRESENTED checkpoint. Quorum signatures show
which keys signed it. Neither proves that no later signed head was published
or that a particular resource effect really committed.

## Bounded research protocol

The new import-only module `scripts/research-quorum-highwater-tip.py`
verifies a supplied exactly-three-member, externally pinned key map and
**two distinct, valid member signatures** on the same canonical, domain
separated resource/term/commit_seq/nonce/receipt-digest/hash-chain head.
It requires a caller-held prior baseline, adjacent hash link continuity,
fixed membership and key epochs, bounded freshness, exact challenge and
an uncompromised public root. It rejects ANY authenticated conflicting
tip rather than letting one quorum mask a fork or stale witness.

Then it uses two stable read-only source/anchor SQLite snapshot pairs
from #390 and checks anchor sequence/nonce against the signed quorum tip.
The verification uses the exact same validated local snapshot for
classification and comparison, without writing data or replaying effects.
Every result sets `release_authorized=false` and `retry_authorized=false`,
even for apparently consistent heads.

**All three synthetic signing keys, pinned roots, challenge, and baseline
are generated/controlled by ONE pytest process on ONE x1-370 host.**
The work DOES NOT create independent custodians or a live latest-tip
service. It is a falsification harness for possible trust boundaries.

## Positive narrow verification

- Two valid distinct signatures and adjacent hash link: accepted for
  research-only observation, never a release/repair authorization.
- One valid witness or the same key counted twice: denied.
- Any validly signed minority conflicting head, even if two other
  signatures agree: denied conservatively as fork or stale conflict.
- Invalid signature, unrecognized member, wrong epoch, stale challenge,
  tampered hash, broken predecessor link, wrong resource digest: denied.
- Locally restored anchor behind a retained current two-signer head:
  `QUORUM_DETECTS_LOCAL_ANCHOR_RESTORE_NO_GO`.

## Material NO-GOs positively reproduced by passing tests

1. **Two older genuine signatures** after anchor restore can be replayed;
   if the independently pinned baseline is also old, the local state again
   appears `SOURCE_COMMITTED_ANCHOR_PENDING_HOLD`. Two signatures, a
   hash chain, and challenge freshness CANNOT establish that the supplied
   tip is globally latest.
2. **Two cooperating/compromised witnesses freshly sign stale history**.
   The same rollback ambiguity persists. Separate *public keys* do not
   imply separate administrators or separate backups.
3. **Root substitution for two witnesses** enables two attacker keys to
   sign plausible prior state, hiding the restore. Key map custody is an
   external authorization problem.
4. **Quorum signs ahead without anchor COMMIT**: on an actual source-only
   crash (anchor never advanced), two synthetic signers claiming seq12
   make the verifier report a local anchor behind them. This has the same
   shape as restore detection — illustrating that the purported witnesses
   also need an independently proven reason and right to attest that an
   anchor COMMIT exists.
5. **Source DBA + signer collusion** can still forge effects and associated
   receipts if actual resource execution is controlled by mutable tables.
6. **All authority remains shared** with one OS/DBA/operator; snapshot
   restore, signer/reviewer compromise, and remote live freshest-tip
   provenance have NOT been tested under real administrative separation.

Even if a majority provides a genuinely recent head, an old minority
conflict makes this verifier fail-closed; it is not an availability or
consensus protocol. It does not fetch current heads from independent
services or persist a protected anti-rollback floor. A valid old quorum
within the time window can still be replayed with matching caller
challenge, and a compromised quorum can issue a fresh old claim.

## Reproduction

    PYTHONPATH=src python3 -m pytest -q -p no:cacheprovider tests/test_research_quorum_highwater_tip.py

The tests run #389's actual self-SIGKILL crash scenarios against SQLite
under pytest tmp_path. Signed heads/keys exist only in test process memory.
No production graph, real PostgreSQL/Neo4j, TPM, credentials, fleet
network, routing, iPhone or service configuration is touched.

## Independent gate still missing

A separate administration/organization must own the durable monotonic
latest-tip witness, signing keys, verifier root protection, key rotation
and backup policy. The resource's true durable COMMIT and effect-time owner
fence must be independently attested, and quorum certificates must be
bound to a causally authorized COMMIT. Falsify scenarios with two colluding
witness administrators, stale replay after quorum restore, term
split-brain, root replacement, unavailable witnesses, privileged DBA
forgery, and source mutation after finalization. A shared fleet SSH/Docker
operator (per prior #378 findings) fails this independence gate.

Retain Auto-Assist #156/#299; Context crypto #46, production graph #31,
hosted CI #45; Auto-Router owner-bound receipts; iOS physical release
holds. **No merge, deploy, production DB/graph, credentials, network,
TPM, failover, model admission, iOS actions, or automatic retry.**
