# Bounded legacy-review orphan audit — READ-ONLY research, release HOLD

## Observed production result: November 3, 2026, 17:38 EST

Fleet Commander reached x1-370's unchanged production API container and
used a Neo4j driver session with READ_ACCESS, only static MATCH/RETURN queries,
and a 150+1 per-category sentinel. It returned:
- 0 Task nodes with kind=intent_review
- 0 Task nodes with kind=approved_intent
- 0 ReviewApprovalOutbox nodes
- no truncation for those specific current-graph queries

The first manual inspection and second run of the new module agreed on zero
records. Neo4j warned that some review/outbox property keys and the new
ReviewApprovalOutbox label did not exist in this database's current schema;
this is consistent with a source-only prototype and not independent proof
of all historical records or external effects.

**Do not infer the old approval route was safe, has never run, or left
no effects in other databases, renamed labels, backups, external Paperclip
or auto-assign infrastructure.** This is bounded current-graph evidence only.

## Source-level legacy defect

Existing POST /api/review/tasks/{task_id}/approve calls
create_task_with_context first, creating a Task with default status READY,
notifying auto-assignment, possibly creating a context packet and dispatching
to Paperclip BEFORE marking source intent_review DONE and linking APPROVED_AS
in a different session. A failure in the gap could leave a child Task or
external effect without completed review evidence. This is a source-level
exposure; no matching incident was seen in the above production sample.

Draft PR #527 introduced a SOURCE-ONLY held candidate and held outbox in
one atomic transaction, but has no real operator identity, admission or
dispatcher. This new audit does NOT reconcile anything or authorize #527.

## New safe inspection utility

src/assistx/review_orphan_audit.py exports audit_review_provenance(driver).
scripts/audit_review_orphans.py exposes a MANUALLY opted-in entrypoint:
    python3 scripts/audit_review_orphans.py --read-only-audit --limit 150

It uses the existing Neo4j connection environment supplied by an authorized
operator, opens an explicit READ_ACCESS session, and performs only static,
8-second-timeout MATCH/RETURN queries. It inspects at most limit+1 rows for
each of the three label/kind categories, and reports truncation, never a
fabricated exhaustive or production-safe verdict. CLI requires a flag and
1 <= limit <= 300; no automatic schedule/endpoint or production service
changes are introduced.

The audit checks:
- intent_review approval decisions with zero or multiple APPROVED_AS children
- approved_intent children whose source pointer is in legacy payload_json or
  new direct approved_from_review_task property (including conflicts, invalid
  JSON and malformed pointers)
- child links to missing, ambiguous or unapproved source review Tasks
- held outbox links and flags that are elevated or not HELD_UNTIL_ADMISSION

Only aggregate counts, status distributions and the explicit decision HOLD
are returned. Raw Task IDs, titles, payloads, reviewer names, source text,
credentials, connection URI and exception messages are never printed.
Malformed/duplicate graph rows fail closed; excessive samples are labeled
TRUNCATED. The CLI exit code 0 means an audit report was emitted, not that
the database is consistent or production dispatch may be enabled.

## Validation — Fleet Commander, x1-370

- 12/12 physical disposable Neo4j tests PASS (empty graph HOLD, finalized
  legacy approvals, missing review, missing edge, malformed payload, double
  child links, held candidate+outbox, elevated/orphan outbox, sample
  truncation and duplicate identity fail-closed).
- 21/21 isolated source/CLI tests PASS, including explicit flag, bounded
  parameters, no application import, credential/exception redaction,
  no mutating Cypher, and no automatic caller.
- Broad combined source/physical graph regression and hosted exact-head CI
  are tracked separately. Disposable tests use unique loopback-only Bolt
  Neo4j containers with synthetic Tasks and no production credentials.

## Known blind spots / remaining P0

This is a bounded, unordered current-graph sample, not a complete historical
census. It does not discover orphan records without known labels or embedded
pointers, external Paperclip/auto-assign effects, archived traces, stale
backups, or direct Bolt principal rewrites. Duplicate/contradictory records
can also be hidden beyond the sampling cap. A clean audit can NEVER prove
global authority, independent signing, anti-rollback or safe cleanup.

Existing review approve/reject/clarify routes remain unmodified.
Authenticated role-bound approval, unique graph indexes, supervised source
review reconciliation, independent receipt custody, global writer privilege
census and owner-signed fleet admission all remain unimplemented or unproven.
Source static writer census remains HOLD with 48 unresolved sites on this
research tree. Five legacy workers still have authenticated Task 401 and
signed production work admission remains expired with zero slots.

**Decision: DRAFT / HOLD / production NO-GO. Never delete, update,
reconcile, READY-promote, auto-dispatch or merge from an audit finding.**
