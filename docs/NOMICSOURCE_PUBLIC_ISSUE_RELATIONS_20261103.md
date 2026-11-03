# Productive local Nomic embeddings — public issue review, November 3 2026

**Public-only research.** Human review required, no automatic issue edits,
no agent-task dispatch, no model load, no external paid APIs, no production
routes, no scheduled inference, no source-body or credential ingestion.

## Scope verified before source collection

Only repositories confirmed PUBLIC through the GitHub connector:
- scottjoyner/auto-assist (25 open issue titles in snapshot)
- scottjoyner/auto-router (14 open issue titles in snapshot)

The public 39-issue title/URL snapshot is frozen in
tests/fixtures/nomicsource_public_open_issues_20261103.json.
Canonical sorted issue-array SHA-256:
2200e97ca5e0ca6b60dad837b776a72bb3213fe39ac6d9fd91559716f54999a9

The data is open-issue titles and canonical URLs from November 3,
2026 only, not live status for future use. Source details and
visibility must be rechecked on subsequent snapshots. The local
research analysis of other visibility scopes is NOT included.

## Actual live local inference, no synthetic token totals

Under strict host-pinned SSH to Xwing, two current resource observations
(RAM/swap/root disk/memory+I/O PSI) and exactly one existing Nomic
Embed Text v1.5 native loaded instance were checked before inference.

One bounded 39-title embeddings request (no generation) completed.
The API returned 39 vectors of 768 dimensions in 0.249 seconds.
The post-filter ranked 20 potential cross-repository relationships,
requiring cosine similarity >=0.58 plus a shared non-generic title term.
All are HUMAN_REVIEW_ONLY. No model was loaded or reconfigured.

The API's reported embedding token usage is NOT trustworthy;
the result deliberately sets verified_embedding_tokens=null.
We measure batches, vectors, sample provenance and time instead of
inventing token consumption or mixing into production token ledgers.

## Grounded leading review relationships

- [Auto-Assist #297](https://github.com/scottjoyner/auto-assist/issues/297)
  and [Auto-Router #37](https://github.com/scottjoyner/auto-router/issues/37):
  cosine 0.84327. Two-human delegation and per-user execution receipts.
  The former issue body already explicitly links the latter: existing
  documented dependency, NOT a new duplicate finding.
- [Auto-Assist #284](https://github.com/scottjoyner/auto-assist/issues/284)
  and [Auto-Router #52](https://github.com/scottjoyner/auto-router/issues/52):
  cosine 0.82205. Both document historic expired canonical generation
  642 and runtime proofs. The current canonical generation is a later
  896 and separately expired; never conflate the two observations.
- [Auto-Assist #304](https://github.com/scottjoyner/auto-assist/issues/304)
  and Auto-Router #52: cosine 0.81652. Stale-manifest freshness
  laundering vs independently fresh runtime reattestation.
  Complementary release requirements, not one automatically closed fix.
- [Auto-Assist #428](https://github.com/scottjoyner/auto-assist/issues/428)
  and [Auto-Router #76](https://github.com/scottjoyner/auto-router/issues/76):
  cosine 0.72795. Hermes transactional claim/revocation fencing vs
  caller-controlled router service-metadata authority. Distinct P0
  claim-fencing controls suitable for coordinated human code review.

Other high-scoring pairs are unverified model suggestions; title
similarity is not proof of causal dependency, issue equivalence or
current severity. No GitHub issue was modified or closed by this work.

## One-shot commands

The input fixture is public-title-only and already checked in.

DRY RUN (zero SSH/embedding requests):

    python3 -B scripts/nomicsource_issue_relations_readonly.py < tests/fixtures/nomicsource_public_open_issues_20261103.json

MANUAL research-only embedding batch (no schedule installed):

    python3 -B scripts/nomicsource_issue_relations_readonly.py --execute < tests/fixtures/nomicsource_public_open_issues_20261103.json

Default dry run sends zero requests. Explicit execution uses
one fixed Nomic model and strict SSH alias, no forwarded credentials,
exactly one localhost /v1/embeddings POST, no generative language
request and no file mutations in the fleet.

The parser enforces a two-repository allowlist, exact canonical
GitHub issue URL format, bounded issue/title counts, duplicate-key
rejection and source ID uniqueness. Remote evidence is nonce-bound,
resource-gated, checked for loaded instance, vector shape/index and
finite similarity. Output links exactly to source GitHub issues;
it cannot authorize production actions.

## Important remaining fleet gate

Production Auto-Router remains degraded with zero enabled free
hosted API providers; canonical generation 896 is expired and
can_dispatch=false. The scheduled 9Router Xwing manual quality HOLD
is unchanged. No model load, worker credentials, GPU lease alteration,
security-policy bypass, graph write or production provider change.
