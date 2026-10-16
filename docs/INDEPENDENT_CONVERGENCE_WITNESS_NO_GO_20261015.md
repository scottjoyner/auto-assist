# Independent convergence witness — synthetic research, NOT an authorization gate

**2026-10-15 | Auto-Assist / Router | NO-GO | No merge, deployment, credential, network, graph, or iOS effects.**

## Background and falsification target

Auto-Assist draft #372 verifies a convergence report's file bytes, nested digest,
and linked manifest. This detects inconsistency, not origin authority: a writer
controlling **both** objects can recompute valid hashes for a fictional effect.
A signed fictional post-COMMIT certificate is also possible if a signing role
is compromised or colludes. Never use signature presence as proof of a resource
commit.

## Experiment

`scripts/prove-independent-convergence-witness.py` is an **import-only**
pure-Python verifier with no production, network, file, mutation, or CLI
acceptance mode. `tests/test_independent_convergence_witness.py` generates
synthetic Ed25519 keys in memory; no real keys, provider endpoints, or model
runtime are accessed.

Its **research-only** consistency evaluation requires:
1. A canonical, domain-separated, Ed25519-signed claim bound to an evidence
   file SHA-256, generation, projection checksum and revision digest.
2. An **out-of-band pinned** resource signing public key; unknown/forged key
   fails, and the source's self-declared key cannot be substituted.
3. A caller-supplied, **independently sourced resource readback** returning
   explicit committed state, generation, checksum, revision, term and effect
   sequence. The same source is queried twice; disagreement is denied.
4. An externally governed, **durable and monotonic high-water anchor**
   (`term, commit_seq, last_nonce`); absent, stale or repeated position fails.
5. Strict audience, type, time-window and nonce limits. The function only
   returns `RESEARCH_CONSISTENCY_ONLY`, **never** a production GO.

## Essential negative results / limitations

- A valid signature for a *fictional* effect fails when the authoritative
  readback is absent, noncommitted, or inconsistent.
- Readback changes between the two observations fail as stale effect/TOCTOU.
- A claim behind an independently held newer term/high-water fails replay.
- **Critical remaining NO-GO:** colluding signer and fake readback callback
  can produce a consistent research result for a **fictional commit**.
  Code cannot establish that an arbitrary callback is independently governed.
- **Critical remaining NO-GO:** rolling back the only high-water anchor
  permits a previously accepted signed witness to pass again. An independently
  monotonic resource (and separate key custody) is required.
- Two matching reads are not a durable resource transaction receipt; the
  effect can change after verification, and no resource-authorized
  commit-fencing or uncertain-commit handling is proven.
- Signatures prove possession of signing keys, not uncorrupted resource
  authority, term ownership, trusted timestamps or real-world commit.
- A compromised or colluding resource signing key defeats certificate-only
  safeguards. This code intentionally cannot be wired into a production
  promotion path or used to update a replay ledger.

## Smallest real-world gate before promotion

An *independent* reviewer must map the actual resource trust boundary, verify
which process owns the effect/term and durable commit sequence, how the public
key is pinned and rotated, how the high-water survives restore, and how an
uncertain COMMIT is treated (fail closed, never automatic retry). Only then
should a separate, authorized read-only physical witness test be planned.
Require hostile cases: signer-key collusion, false committed bit, readback
lying, stale effect swap, lost ledger, same nonce, restored snapshots and
privileged SQL/graph bypass. Preserve the existing Knowledge/Kipnerter
cryptographic NO-GO findings; no coordination/split-brain promotion.

Related drafts: Auto-Assist #368–#372; transport/custody issues #299 and
#156; Knowledge's separate context-broker false-certificate/rollback research.
