# #148 read-only uncertain in-flight effect reconciliation — research gate

Date: 2026-10-09. Disposition: **research-only / draft / distributed failover NO-GO**.

## Placement

Stack after draft #231 (single independent disposable witness) and #230
(receipt pins). The preceding research makes two important counterexamples
explicit: copying the witness reproduces split-brain, and a caller can spoof
its holder field. Neither problem is repaired here.

## Added contract

`src/assistx/trace_effect_reconciliation_research.py` is a *pure function*,
without database, network, clock, API, routing, credential, or execution calls.
It consumes caller-supplied synthetic receiver receipts bound to domain,
operation, effect, owner, and term. It produces an observation classification
and ALWAYS sets `automatic_replay_allowed=False` and `takeover_allowed=False`.

| Classification | Interpretation | Permitted action |
| --- | --- | --- |
| `confirmed-applied` | Durable atomic receiver record says effect applied | Record observation; do not replay |
| `confirmed-aborted` | Durable terminal receiver record says effect did not apply | Record observation; do not automatically retry |
| `unknown-hold` | Missing, not-seen, or nonterminal evidence | Hold for independent reconciliation |
| `conflicting-evidence-hold` | Mismatched identities or contradictory terminal records | Hold and investigate integrity |
| `untrusted-evidence-hold` | Missing durable atomic boundary / malformed data | Hold; do not trust |
| `stale-effect-violation` | Receiver claims application with term below its high watermark AT COMMIT | Safety failure: investigate |

A term-1 effect durably applied before term-2 takeover is not a violation:
comparing with the *current* term would misclassify a legitimate past effect.
The receiver must durably bind the term check to the effect in one commit.

## Key limitations and requirements to move beyond read-only

1. **Receipt trust:** A `durable=True` input field is only a synthetic fixture
   assertion. The code cannot attest receipt authenticity, durability,
   completeness, or the independence of any witness. Implement authenticated
   custody and verify real effect-sink proofs before trusting classifications.
2. **Atomic receiver fencing:** PR #231 performs atomic checks only inside its
   own *synthetic* SQLite witness. Real Neo4j/Redis/executors/providers must
   enforce the term at their own commit boundary, not in a separate preflight.
3. **No absence from silence:** `not-seen` is never proof an effect did not
   occur. A confirmed abort requires terminal durable receiver testimony.
4. **Recovery control:** Neither `confirmed-aborted` nor `confirmed-applied`
   authorizes replay, capacity release, leadership promotion, or another
   effect. Those are independent, currently blocked authorization decisions.
5. **No history authenticity:** No consensus, independent monotonic term
   custody, partition negative proof, producer/holder attestation, or live
   physical-node test is added.
6. **No production wiring:** No service files, listeners, prod storage,
   Neo4j, Redis, provider, graph, API, NAS, router, or SSH state changes.

## Next explicit gate

Prove receipt authenticity and completeness at a *mock* independent receiver,
with negative tests for restored witness, forged holder, rejected stale
term at commit, lost acknowledgment, contradictory receipts, and completed
work preceding new ownership. Then require independent review, exact-head
checks, physical negative acceptance, and separate staging approval before
considering any non-synthetic receiver integration.
