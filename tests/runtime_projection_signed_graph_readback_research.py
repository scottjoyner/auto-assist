"""Synthetic-only independently signed graph readback and ledger reconciliation.

This cannot observe a graph itself or authorize graph writes. A signed claim is
only as trustworthy as its separately custodied witness issuer and graph access.
No production bootstrap, high-water authority or automatic failover is provided.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from runtime_projection_offline_admission_ledger import AdmissionHold, _connect

from scripts.runtime_projection_signed_apply_gate import (
    CLOCK_SKEW_MS,
    canonical,
    exact,
    integer,
    validate,
    verify_signature,
)

WITNESS_AUDIENCE = "assistx-independent-graph-readback-research-v1"
WITNESS_MAX_AGE_MS = 30_000


@dataclass(frozen=True)
class SyntheticReadback:
    generation: int
    approval_id: str
    pending_cleared_in_offline_ledger: bool
    production_graph_commit_proven: bool = False
    production_routing_authorized: bool = False


def verify_sealed_readback(
    *,
    manifest: dict, attestation: dict,
    pinned_source_key: bytes, pinned_reviewer_key: bytes,
    sealed_readback: dict, pinned_observer_key: bytes,
    trusted_now_ms: int,
):
    if (pinned_observer_key in (pinned_source_key, pinned_reviewer_key)
            or pinned_source_key == pinned_reviewer_key):
        raise AdmissionHold("INDEPENDENT_OBSERVER_KEY_REQUIRED")
    proof = validate(
        manifest, attestation, pinned_source_key,
        pinned_reviewer_key, trusted_now_ms,
    )
    payload = verify_signature(
        sealed_readback, pinned_observer_key, "independent graph readback")
    exact(payload, {
        "audience", "role", "manifest_sha256", "source_receipt_sha256",
        "approval_id", "generation", "previous_generation",
        "canonical_generation", "canonical_status",
        "canonical_updated_at_ms", "canonical_expires_at_ms",
        "matching_canonical_rows", "matching_approval_receipts",
        "receipt_generation", "receipt_source_receipt_sha256",
        "observed_at_ms", "expires_at_ms",
    }, "sealed graph readback")
    if (payload["audience"] != WITNESS_AUDIENCE
            or payload["role"] != "independent-graph-observer"
            or payload["manifest_sha256"] != hashlib.sha256(
                canonical(manifest)).hexdigest()
            or payload["source_receipt_sha256"] != proof.source_sha256
            or payload["receipt_source_receipt_sha256"] != proof.source_sha256
            or payload["approval_id"] != proof.approval_id
            or type(payload["generation"]) is not int
            or payload["generation"] != proof.generation
            or type(payload["previous_generation"]) is not int
            or payload["previous_generation"] != manifest["expected_current_generation"]
            or type(payload["canonical_generation"]) is not int
            or payload["canonical_generation"] != proof.generation
            or payload["canonical_status"] != "approved"
            or type(payload["receipt_generation"]) is not int
            or payload["receipt_generation"] != proof.generation
            or type(payload["matching_canonical_rows"]) is not int
            or payload["matching_canonical_rows"] != 1
            or type(payload["matching_approval_receipts"]) is not int
            or payload["matching_approval_receipts"] != 1):
        raise AdmissionHold("READBACK_IDENTITY_OR_RECEIPT_MISMATCH")
    seen = integer(payload["observed_at_ms"], "readback observed")
    expires = integer(payload["expires_at_ms"], "readback expires")
    if (seen > trusted_now_ms + CLOCK_SKEW_MS
            or trusted_now_ms - seen > WITNESS_MAX_AGE_MS
            or not seen < expires <= seen + WITNESS_MAX_AGE_MS
            or expires <= trusted_now_ms):
        raise AdmissionHold("READBACK_STALE_FUTURE_OR_EXPIRED")
    updated = integer(payload["canonical_updated_at_ms"], "canonical updated")
    canonical_expiry = integer(payload["canonical_expires_at_ms"],
                               "canonical expiry")
    if (updated < proof.approved_at_ms - CLOCK_SKEW_MS
            or updated > seen + CLOCK_SKEW_MS
            or abs(canonical_expiry - updated - manifest["ttl_seconds"] * 1000)
            > CLOCK_SKEW_MS
            or canonical_expiry <= trusted_now_ms
            or canonical_expiry > updated + 900_000):
        raise AdmissionHold("READBACK_CANONICAL_LEASE_MISMATCH")
    return proof


def reconcile_synthetic_readback(
    db_path: Path, *, manifest: dict, attestation: dict,
    pinned_source_key: bytes, pinned_reviewer_key: bytes,
    sealed_readback: dict, pinned_observer_key: bytes,
    trusted_now_ms: int,
) -> SyntheticReadback:
    """Advance only an isolated SQLite fixture after a triple-signed claim.

    Not a production graph witness: independent source-of-truth provenance,
    denial of alternate admin Bolt clients and multi-node custody remain HOLD.
    """
    proof = verify_sealed_readback(
        manifest=manifest, attestation=attestation,
        pinned_source_key=pinned_source_key,
        pinned_reviewer_key=pinned_reviewer_key,
        sealed_readback=sealed_readback,
        pinned_observer_key=pinned_observer_key,
        trusted_now_ms=trusted_now_ms,
    )
    expected = manifest["expected_current_generation"]
    with _connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT last_confirmed_generation, pending_approval_id, pending_generation "
            "FROM custody WHERE singleton=1").fetchone()
        if (current is None or current != (expected, proof.approval_id,
                                           proof.generation)):
            raise AdmissionHold("NO_EXACT_UNRESOLVED_GENERATION_OR_APPROVAL")
        existing = db.execute(
            "SELECT source_sha256,state FROM replay WHERE approval_id=?",
            (proof.approval_id,),
        ).fetchone()
        if existing != (proof.source_sha256, "PENDING_UNCERTAIN_GRAPH_OUTCOME"):
            raise AdmissionHold("REPLAY_PROVENANCE_OR_PENDING_STATE_MISMATCH")
        db.execute(
            "UPDATE replay SET state='CONFIRMED_SYNTHETIC_READBACK' "
            "WHERE approval_id=? AND source_sha256=? "
            "AND state='PENDING_UNCERTAIN_GRAPH_OUTCOME'",
            (proof.approval_id, proof.source_sha256),
        )
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise AdmissionHold("REPLAY_CAS_REJECTED")
        db.execute(
            "UPDATE custody SET last_confirmed_generation=?, "
            "pending_approval_id=NULL,pending_generation=NULL "
            "WHERE singleton=1 AND last_confirmed_generation=? "
            "AND pending_approval_id=? AND pending_generation=?",
            (proof.generation, expected, proof.approval_id, proof.generation),
        )
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise AdmissionHold("HIGH_WATER_CAS_REJECTED")
        db.execute("COMMIT")
    return SyntheticReadback(
        generation=proof.generation,
        approval_id=proof.approval_id,
        pending_cleared_in_offline_ledger=True,
    )
