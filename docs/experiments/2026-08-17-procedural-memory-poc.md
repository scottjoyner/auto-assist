# Procedural Memory Promotion PoC — 2026-08-17

Issue: `#25`
Branch: `agent/procedural-memory-poc`

## Hypothesis

Coding-agent lessons should not become durable procedural memory after a single successful session. A candidate rule should retain source-run provenance, positive and negative outcomes, confidence, and pass minimum support/success thresholds before it is even eligible for eval-gated promotion.

## Implemented baseline

Added a dependency-free `ProceduralMemoryCandidate` contract with:
- rule text;
- source run IDs;
- positive/negative outcomes;
- confidence;
- scope;
- support and observed success-rate calculations.

The default eligibility gate requires:
- at least three observed outcomes;
- at least 75% success rate;
- at least 0.70 confidence;
- non-empty source-run provenance.

Eligibility is **not** production promotion. A candidate that passes still must be tested on held-out tasks through #24 before durable activation.

## Next stages

1. Import sanitized coding-session outcomes from the current AssistX history and/or cass-memory-compatible exports.
2. Generate candidate rules without storing raw chain-of-thought.
3. Preserve source run/outcome IDs as graph provenance.
4. Evaluate rules on held-out coding tasks.
5. Store accepted rules in Neo4j with freshness, confidence and invalidation state.
6. Test negative outcomes and supersession so bad/stale procedures can be demoted without erasing history.

## Guardrail

Procedural memory is advisory context. It cannot override task admission, tool security, runtime routing, repository policy, or operator-controlled publication.
