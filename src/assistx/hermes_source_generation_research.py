"""Opt-in source generation transaction contract (research, NO-GO).

No production feature flag or implicit migration. Only participating writers
serialize under this ledger. Dynamic, legacy, and external Task writers do not.
This module NEVER asserts a complete census, external custody or deployment
eligibility. Live services do not import or invoke it.

An independent operator must explicitly install the ledger's uniqueness
constraint and seed its singleton in an isolated database before use.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .hermes_claim_census import MAX_ROWS, _pass

OWNER = "hermes-local"
CONSTRAINT_NAME = "hermes_source_generation_owner_unique"


class GenerationProtocolError(ValueError):
    """Non-sensitive, fail-closed source-generation rejection."""


def _check_constraint(session: Any) -> None:
    row = session.run(
        """
        SHOW CONSTRAINTS
        YIELD name, type, labelsOrTypes, properties
        WHERE name=$name
        RETURN name, type, labelsOrTypes, properties
        LIMIT 1
        """,
        {"name": CONSTRAINT_NAME},
    ).single()
    if (not row or row.get("type") != "UNIQUENESS"
            or list(row.get("labelsOrTypes") or []) != ["HermesSourceGeneration"]
            or list(row.get("properties") or []) != ["owner"]):
        raise GenerationProtocolError("generation_uniqueness_constraint_missing")


def _lock(tx: Any) -> int:
    # A real node write lock acquired inside the same Neo4j transaction is
    # held until commit/rollback. All participating writers & snapshots
    # must acquire this *same* lock, not per-Task lock alone.
    row = tx.run(
        """
        MATCH (g:HermesSourceGeneration {owner:$owner})
        SET g.lock_epoch=coalesce(g.lock_epoch,0)+1
        WITH g
        RETURN g.generation AS generation, g.owner AS owner
        """,
        {"owner": OWNER},
    ).single()
    if not row or row.get("owner") != OWNER:
        raise GenerationProtocolError("generation_singleton_absent")
    gen = row.get("generation")
    if type(gen) is not int or gen < 0:
        raise GenerationProtocolError("invalid_source_generation")
    return gen


def advance_candidate_generation(
    session: Any,
    *,
    expected_generation: int,
    mutate: Callable[[Any], bool],
) -> dict[str, Any]:
    """Execute a candidate Task mutation and ledger increment atomically.

    This validates a supplied callback's ACK only. It does NOT verify that
    the callback changes exactly one owner-authorized Task or that all other
    fleet writers are participating. No production callers are wired.
    """
    if (type(expected_generation) is not int
            or expected_generation < 0
            or not callable(mutate)):
        raise GenerationProtocolError("invalid_generation_request")
    _check_constraint(session)

    def _tx(tx: Any) -> int:
        current = _lock(tx)
        if current != expected_generation:
            raise GenerationProtocolError("stale_source_generation")
        if mutate(tx) is not True:
            raise GenerationProtocolError("task_mutation_not_acknowledged")
        row = tx.run(
            """
            MATCH (g:HermesSourceGeneration {owner:$owner})
            SET g.generation=$next
            RETURN g.generation AS generation
            """,
            {"owner": OWNER, "next": current + 1},
        ).single()
        if not row or row.get("generation") != current + 1:
            raise GenerationProtocolError("generation_commit_not_acknowledged")
        return current + 1

    generation = session.execute_write(_tx)
    return {
        "owner": OWNER, "generation": generation,
        "participating_write_committed": True,
        "writer_registry_complete": False,
        "independent_custody_verified": False,
        "resource_effects_verified": False,
        "production_dispatch_authorized": False,
        "decision": "HOLD",
    }


def pinned_candidate_snapshot(
    session: Any,
    *,
    max_rows: int = MAX_ROWS,
) -> dict[str, Any]:
    """Read a bounded, *participating-writer-locked* claim candidate.

    Atomic only relative to users of this helper, NOT legacy or external
    writes. An equal digest still does not prove erased-history completeness.
    """
    result: dict[str, Any] = {
        "schema": 1,
        "owner": OWNER,
        "snapshot_generation": None,
        "candidate_count": None,
        "candidate_digest_sha256": None,
        "candidate_source_lock_acquired": False,
        "authoritative_complete_census": False,
        "all_writers_generation_fenced": False,
        "external_high_water_verified": False,
        "independent_custody_verified": False,
        "resource_effects_verified": False,
        "production_dispatch_authorized": False,
        "decision": "HOLD",
        "reason": "unverified_source_generation_and_writer_coverage",
    }
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
        result["reason"] = "invalid_census_bound"
        return result

    try:
        _check_constraint(session)

        def _tx(tx: Any) -> tuple[int, int, str]:
            current = _lock(tx)
            rows, digest = _pass(tx, max_rows)
            return current, len(rows), digest

        generation, count, digest = session.execute_write(_tx)
    except Exception:
        # No task content, raw query, or credential details on failure.
        result["reason"] = "unqualified_source_or_claim_inventory"
        return result

    result.update({
        "snapshot_generation": generation,
        "candidate_count": count,
        "candidate_digest_sha256": digest,
        "candidate_source_lock_acquired": True,
    })
    return result
