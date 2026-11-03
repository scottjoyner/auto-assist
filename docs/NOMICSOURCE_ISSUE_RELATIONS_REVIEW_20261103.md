# Source-linked Nomic issue relationships — November 3, 2026

**Manual one-shot, human-review-only, free-local, nonproduction.**
No issue edits, merges, task assignments, model loading, paid API usage,
production routing, automatic scheduling, or release approval.
The existing 9Router Xwing manual quality hold remains unchanged.

## Verified input and model work

Live source: GitHub open issues, queried November 3, 2026.
Only public issue TITLES, canonical GitHub issue URLs, repo names and
numbers were passed, not issue bodies or protected private data.

73 open issues: Auto-Assist 25, Auto-Router 14, SensorGraft 9,
Knowledge 25. Canonical issue-array SHA-256:
51864df04e2884ecb1c07778a246d8c263f26a113cccfbf6bf4f401fda346823

Xwing native API confirmed exactly one already-loaded Nomic Embed
Text v1.5 instance. Strict host-key-checked SSH, no forwarded agent,
two short host RAM/swap/PSI/disk samples, localhost-only GET for native
catalog and a single bounded localhost POST for 73 embeddings.

Real first batch: 73 embeddings, 768 dimensions, 1.165 s.
Real second batch, after tightening lexical filters:
73 embeddings, 768 dimensions, 0.538 s.
The second run produced 14 cross-repository HUMAN_REVIEW_ONLY
suggestions with cosine >=0.58 and >=1 shared nontrivial title token.
These are review suggestions, NEVER confirmed duplicates or current
production dependencies. Live GitHub status can change later.

The embedding API did NOT provide a trusted token usage count;
verified_embedding_tokens is NULL, not a zero or estimated value.
Request count and batch duration are measured. They are not tokens.

## Reviewed, source-backed cross-repository examples

- Auto-Assist [#297](https://github.com/scottjoyner/auto-assist/issues/297)
  and Auto-Router [#37](https://github.com/scottjoyner/auto-router/issues/37).
  Cosine 0.84327. The AssistX issue ALREADY explicitly links #37:
  confirmed prior cross-repo dependency, NOT a newly found duplicate.
  Topic: two-human gateway identity and local execution receipts.

- Auto-Assist [#284](https://github.com/scottjoyner/auto-assist/issues/284)
  and Auto-Router [#52](https://github.com/scottjoyner/auto-router/issues/52).
  Cosine 0.82205. Both describe historic expired generation 642 and
  invalid runtime witnesses. Related incident area. The currently
  observed canonical generation is 896 and also expired; do not
  conflate historical and current generations.

- Auto-Assist [#304](https://github.com/scottjoyner/auto-assist/issues/304)
  and Auto-Router [#52](https://github.com/scottjoyner/auto-router/issues/52).
  Cosine 0.81652. #304 concerns stale-manifest freshness laundering
  even with a CAS; #52 requires current independent runtime proofs.
  Complementary controls; two separately required fixes.

- Auto-Assist [#501](https://github.com/scottjoyner/auto-assist/issues/501)
  and Knowledge [#55](https://github.com/scottjoyner/knowledge/issues/55).
  Cosine 0.75725. #501 confirms 401 responses for five node agents
  lacking scoped machine identities. #55 requires independent agent
  action provenance/witness. Separate gates, not duplicates.

- SensorGraft [#1](https://github.com/scottjoyner/sensorgraft/issues/1)
  and Knowledge [#97](https://github.com/scottjoyner/knowledge/issues/97).
  Cosine 0.73852. Both concern GitHub Actions billing/hosted CI.
  Recheck up-to-date billing and workflow status before action.

- Auto-Assist [#428](https://github.com/scottjoyner/auto-assist/issues/428)
  and Auto-Router [#76](https://github.com/scottjoyner/auto-router/issues/76).
  Cosine 0.72795. Hermes transactional claim-ID revocation fencing
  vs caller-controlled service metadata bypass of router claim fence.
  Shared review dependency, separate vulnerabilities and fixes.

Other pair candidates remain UNVERIFIED suggestions in the one-shot
JSON output. No automatic duplicate labels or GitHub comments.

## Reproduction

Create a current UTF-8 JSON input with schema
assistx.open-issue-matches.input.v1 and issues list of exact
repo, number, title and source_url fields, each URL matching
https://github.com/scottjoyner/REPO/issues/NUMBER.
Allowed repositories are auto-assist, auto-router, sensorgraft and
knowledge. Maximum 100 current issue titles.

Dry run, sends ZERO inference requests:

    python3 -B scripts/nomicsource_issue_relations_readonly.py < open-issues.json

Explicit one-shot manual local research batch:

    python3 -B scripts/nomicsource_issue_relations_readonly.py --execute < open-issues.json

The tool applies resource/loaded-model preflight and fails closed
on SSH errors, malformed/duplicate JSON, wrong nonce, wrong model,
bad vector count/dimension, unsupported authority claims, and missing
source provenance. Output includes source links, similarity, shared
title subject, and human-review-only status. It never posts issue
comments, calls AssistX task APIs or writes Neo4j.
No timer or production provider was installed.

## Gate boundaries

One embedding request is not 24-hour fleet-wide token accounting.
Do not add unproven embedding token estimates to existing 9Router,
Auto-Router or other manual token ledgers. Runtime process identity,
independent custody, machine credentials, GPU lease policy, and
canonical production admission remain separate explicit gates.
Auto-Router remains degraded and signed generation 896 expired.
