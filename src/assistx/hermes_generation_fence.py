"""Research-only Hermes census/write-fence transaction protocol.

This is not a release authorization system. A Neo4j database coordinator
can be rolled back by its administrator, and only writers which acquire
the SAME fence lock in the SAME transaction are serialized.

No production caller, API endpoint, scheduled job or dispatch path invokes
this module. A real source-authoritative census requires that every
Task writer participate and an independently administered external head.
"""
from __future__ import annotations

from typing import Any, Callable

from . import hermes_claim_census as legacy

OWNER = legacy.OWNER
SCHEMA = 1
LOCK_AND_READ = """
MATCH (g:HermesCensusGeneration {owner:$owner})
SET g.read_gate = coalesce(g.read_gate, 0) + 1
RETURN g.generation AS generation
"""
LOCK_AND_ADVANCE_WRITER = """
MATCH (g:HermesCensusGeneration {owner:$owner})
SET g.generation=coalesce(g.generation,0)+1
RETURN g.generation AS generation
"""
VALIDATE_GENERATION = """
MATCH (g:HermesCensusGeneration {owner:$owner})
RETURN g.generation AS generation
"""


def _generation(record: object) -> int:
    if record is None:
        raise ValueError("generation_head_missing")
    n = record["generation"]
    if type(n) is not int or n < 0:
        raise ValueError("generation_head_invalid")
    return n


def lock_writer_before_mutation(tx: Any, *, owner: str = OWNER) -> int:
    """Acquire coordination lock BEFORE all claim changes in same tx.

    An untrusted or different owner is forbidden; if the caller commits
    a writer mutation without this lock it is OUTSIDE the protocol.
    No way for this helper to enforce use across the whole codebase.
    """
    if owner != OWNER:
        raise ValueError("source_owner_mismatch")
    return _generation(tx.run(LOCK_AND_ADVANCE_WRITER, {"owner": owner}).single())


def generation_locked_candidate(
    session_factory: Any, *, max_rows: int = legacy.MAX_ROWS,
    on_locked_for_testing: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Opt-in DB write-lock experiment; outputs HOLD regardless of result.

    Deliberately acquires a real write lock on the census-generation node:
    NOT read-only, not safe to run against production or operator-supplied
    databases. The optional callback is for deterministic disposable races.
    """
    count = None
    digest = None
    generation = None
    reason = "experiment_not_qualified"
    observed = False
    if type(max_rows) is not int or not 1 <= max_rows <= legacy.MAX_ROWS:
        reason = "invalid_census_bound"
    elif on_locked_for_testing is not None and not callable(on_locked_for_testing):
        reason = "invalid_test_barrier"
    else:
        try:
            with session_factory() as session:
                with session.begin_transaction() as tx:
                    generation = _generation(
                        tx.run(LOCK_AND_READ, {"owner": OWNER}).single()
                    )
                    if on_locked_for_testing is not None:
                        on_locked_for_testing()
                    # A single bounded scan in the locked transaction.
                    rows, digest = legacy._pass(tx, max_rows)
                    check = _generation(
                        tx.run(VALIDATE_GENERATION, {"owner": OWNER}).single()
                    )
                    if check != generation:
                        raise ValueError("generation_changed_inside_scan")
                    count = len(rows)
                    observed = True
                    tx.commit()
            reason = "test_only_writer_coverage_and_external_anchor_unverified"
        except Exception:
            # Missing lock node, a failed transaction, overflow and malformed
            # records are denied. No raw rows or exception text escapes.
            reason = "generation_lock_or_claim_census_unqualified"
            count = None
            digest = None
            generation = None
            observed = False
    return {
        "schema": SCHEMA,
        "mode": "hermes_disposable_generation_locked_census_candidate",
        "owner": OWNER,
        "experiment_transaction_completed": observed,
        "candidate_count": count,
        "candidate_digest_sha256": digest,
        "candidate_generation": generation,
        "reason": reason,
        "all_claim_writers_joined_generation_fence": False,
        "source_generation_fenced": False,
        "outside_fleet_monotonic_head_verified": False,
        "admin_rollback_protected": False,
        "source_owner_complete_census_verified": False,
        "production_dispatch_authorized": False,
        "production_rollback_authorized": False,
        "decision": "HOLD",
    }
