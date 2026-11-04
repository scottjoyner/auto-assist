# Single-query private subject consistency — read-only research v1

**DRAFT. NO LIVE MIGRATION, GRAPH WRITES, CREDENTIAL CHANGES, OR PRODUCTION APPROVAL.**

## The problem addressed

The earlier private Neo4j structural receipt and owner-overlap receipt were
obtained in separate read-only transactions. Identical aggregate counts and
self-administered SHA-256 checks do not establish that both observations came
from the same endpoint identity or a concurrent-writer-free snapshot.

An opt-in read-only, parameter-free Cypher statement was executed inside the
running AssistX API container with its image and container identity pinned
before and after. That statement selects the duplicate endpoint_id group and,
within the **same statement**, returns the two nodes' labels, property-key
names, adjacent relationship types/directions/neighbor-label/property-key sets,
two EXPOSES-owner counts, shared owner count and SERVES-model edge counts.
It does **not** return endpoint_id, a Neo4j elementId, node properties' values,
owner IDs, model IDs, credentials or neighboring record values.

The private result confirmed two nodes, 9/17 keys, seven shared key names,
edge counts 1/2, one shared owner, and SERVES counts 0/1. This improves
same-query *subject linkage*. Neo4j read-committed semantics and concurrent
writers still prevent a guarantee of a serializable or frozen snapshot.

## New research validator

scripts/endpoint_identity_one_query_review.py extends the original three-file
offline disposition verifier with a fourth privately held single-query result.
All four receipts must be owner-only mode 0600 regular files, not symlinks,
and smaller than 128 KiB. It checks the supplied SHA-256 of the fourth file,
requires a timezone-aware observation no more than one hour old (and at most
60 seconds in the future by the local computer clock), validates exact
non-authority and no-value flags, compares all 9/17 property names and
EXPOSES/SERVES relationship signatures to the old subject receipt and private
review, and compares the owner/model counters to the old overlap receipt.

Example using only private local paths, never GitHub or CI:

    python3 scripts/endpoint_identity_one_query_review.py \
       --private-review "$PRIVATE_REVIEW_PATH" \
       --private-shape "$PRIVATE_SHAPE_PATH" \
       --private-owners "$PRIVATE_OWNERS_PATH" \
       --private-one-query "$PRIVATE_SINGLE_QUERY_PATH" \
       --expected-one-query-sha256 "$OPERATOR_PINNED_RECEIPT_DIGEST"

The fourth receipt SHA must be independently checked and pinned by the
reviewing operator to be meaningful. Merely copying a SHA from the same
fleet-controlled source does not make custody external. Neither digest nor
the local clock prove signer independence, historical high-water retention,
absence of forks, graph quiescence or data-value semantics.

All supported outcomes are non-authorizing:

- Default/missing evidence: HOLD_FOUR_PRIVATE_EVIDENCE_INPUTS_REQUIRED, exit 2
- Matching four documents: HOLD_ONE_QUERY_CONSISTENT_BUT_UNAPPROVED, exit 2
- Mismatched or stale input: DENY_EVIDENCE_DRIFT, exit 3
- No success/GO result is implemented

The checker only opens four explicit local files. It does not invoke Docker,
Neo4j, SSH, network calls, writes, migration DDL or a production service.

## Controlled test and acceptance limitations

Run synthetic test cases:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m pytest -q \
      tests/test_endpoint_identity_one_query_review.py

The tests include wrong subject, owner count, SERVES edge, property key,
relationship label, stale/future timestamp, digest substitution, unsupported
metadata, missing evidence, and coherent same-fleet-administrator forgery.
Even when all receipts have been rewritten together, the output still denies
release and explicit outside-operator attestation.

Both the source and the claimed producer-query method are currently held
within the same administrator boundary. A metadata field saying
single-statement is not independently attested execution of that statement.
If the one-hour window expires, take another authorized, bounded, read-only
observation and repeat human review: never extend the time window silently
or change the host clock to bypass the denial.

The real 26 property dispositions, consumer references, owner approval,
survivor selection, writer quiescence, restricted runtime DB roles, actual
data-shaped recovery, and independent SGNC custody remain unresolved.
Keep Auto-Assist #322/#318 and SensorGraft #51 OPEN, PRs draft/unmerged,
and production NO-GO.
