"""Synthetic-only replay qualification: never admits a graph write.

No import from the active AssistX runtime, no API route, and no database I/O.
The existing event ledger lacks a transactional projection completion receipt;
a matching event digest in the ledger alone cannot prove that projection ran.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplayWitness:
    event_present: bool
    digest_matches: bool | None
    projection_receipt_present: bool
    projection_receipt_digest_matches: bool | None
    projection_transaction_committed: bool | None
    writer_epoch_fenced: bool
    producer_owner_verified: bool
    queued_events_drained: bool
    source_revision_bound: bool
    positive_epoch: int | None
    synthetic_only: bool


@dataclass(frozen=True)
class ReplayDecision:
    status: str
    projection_confirmed: bool = False
    event_replay_authorized: bool = False
    production_release_authorized: bool = False


def qualify_replay(witness: Any) -> ReplayDecision:
    """Denial-only decision; neither a projection receipt nor a fence grants replay.

    The receipt must have been produced by the same atomic DB transaction as
    the graph projection. A separate write of a completion boolean is not proof.
    """
    if not isinstance(witness, ReplayWitness) or witness.synthetic_only is not True:
        return ReplayDecision("HOLD_UNTRUSTED_OR_NON_SYNTHETIC_WITNESS")
    if (
        type(witness.positive_epoch) is not int
        or witness.positive_epoch <= 0
        or witness.source_revision_bound is not True
        or witness.producer_owner_verified is not True
        or witness.writer_epoch_fenced is not True
        or witness.queued_events_drained is not True
    ):
        return ReplayDecision("HOLD_FENCE_PRODUCER_DRAIN_OR_SOURCE_UNVERIFIED")
    if witness.event_present is not True:
        return ReplayDecision("HOLD_EVENT_ABSENT_NOT_A_REPLAY")
    if witness.digest_matches is not True:
        return ReplayDecision("HOLD_DIGEST_CONFLICT_OR_UNKNOWN")
    if (
        witness.projection_receipt_present is not True
        or witness.projection_receipt_digest_matches is not True
        or witness.projection_transaction_committed is not True
    ):
        return ReplayDecision("HOLD_UNKNOWN_PROJECTION_EFFECT_NO_AUTOREPLAY")
    return ReplayDecision("CONFIRMED_PROJECTED_NO_REPLAY_REQUIRED", projection_confirmed=True)
