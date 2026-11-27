"""Optional, durable direct-claim admission gate.

A pre-provisioned singleton is required when enabled. New claims and a gate
change must hold the same Neo4j write lock until transaction commit. This
does not cover RQ, passive claims, external dispatchers or legacy binaries.
"""
from contextlib import contextmanager
import os
from neo4j.exceptions import ResultNotSingleError

LOCK_QUERY = """
MATCH (g:DirectClaimAdmission {id:'global'})
SET g.lock_sequence = coalesce(g.lock_sequence, 0) + 1
RETURN g.mode AS mode, g.revision AS revision
"""


@contextmanager
def direct_claim_scope(session):
    setting = os.getenv("ASSISTX_DIRECT_CLAIM_FENCE_REQUIRED", "false").strip().lower()
    if setting in {"false", "0", "no", "off"}:
        yield session, "legacy"
        return
    if setting not in {"true", "1", "yes", "on"}:
        yield session, "unavailable"
        return
    with session.begin_transaction(timeout=10) as tx:
        try:
            row = tx.run(LOCK_QUERY).single(strict=True)
        except ResultNotSingleError:
            row = None
        mode = row.get("mode") if row else None
        revision = row.get("revision") if row else None
        if mode not in {"open", "closed"} or type(revision) is not int or revision < 0:
            yield tx, "unavailable"
            return
        yield tx, mode
        tx.commit()


def set_direct_claim_admission(session, *, mode, expected_revision):
    """Internal operator primitive; intentionally not an exposed API.

    Caller must establish authorization and provision a unique singleton.
    Compare-and-set prevents a stale maintenance request reopening a gate.
    """
    if mode not in {"open", "closed"} or type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid direct admission change")
    with session.begin_transaction(timeout=10) as tx:
        try:
            row = tx.run(LOCK_QUERY).single(strict=True)
        except ResultNotSingleError:
            row = None
        if not row or row.get("mode") not in {"open", "closed"} or type(row.get("revision")) is not int or row.get("revision") != expected_revision:
            return {"changed": False, "reason": "revision_conflict_or_missing"}
        updated = tx.run(
            """MATCH (g:DirectClaimAdmission {id:'global'})
            SET g.mode=$mode, g.revision=$revision, g.updated_at_ts=timestamp()
            RETURN g.mode AS mode, g.revision AS revision""",
            {"mode": mode, "revision": expected_revision + 1},
        ).single()
        if not updated or updated.get("mode") != mode or updated.get("revision") != expected_revision + 1:
            raise RuntimeError("direct admission update not witnessed")
        tx.commit()
        return {"changed": True, "mode": mode, "revision": expected_revision + 1}
