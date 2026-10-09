"""Read-only, synthetic uncertain-effect classification for #148 research.

NO production adapter, authority, election, I/O, automatic replay or takeover.
Inputs are supplied snapshots, NOT independently authenticated evidence. The
caller must establish receipt provenance, custody and durability separately.
This module models the decisions a future receiver must support; it does not
prove that Neo4j, Redis, providers or executors enforce any fencing term.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal


class Classification(str, Enum):
    APPLIED = "confirmed-applied"
    ABORTED = "confirmed-aborted"
    UNKNOWN = "unknown-hold"
    CONFLICT = "conflicting-evidence-hold"
    UNTRUSTED = "untrusted-evidence-hold"
    STALE_APPLIED = "stale-effect-violation"


ReceiptStatus = Literal["applied", "aborted", "unknown", "not-seen", "rejected-stale"]


@dataclass(frozen=True)
class Operation:
    domain: str
    operation_id: str
    effect_id: str
    owner: str
    term: int


@dataclass(frozen=True)
class ReceiverReceipt:
    """Synthetic receiver testimony, not a signature or trusted remote result.

    `boundary_term` is the durable high-watermark observed AT the effect's
    atomic decision. Its existence is only an assumption in these fixtures.
    """

    domain: str
    operation_id: str
    effect_id: str
    owner: str
    term: int
    receipt_id: str
    status: ReceiptStatus
    durable: bool
    boundary_term: int | None
    # A terminal abort must have a receiver-side durable no-effect decision;
    # merely observing a missing entry is NOT absence proof.
    terminal: bool = False


@dataclass(frozen=True)
class Decision:
    classification: Classification
    reason: str
    automatic_replay_allowed: bool = False
    takeover_allowed: bool = False


def reconcile(operation: Operation, receipts: Iterable[ReceiverReceipt]) -> Decision:
    """Classify only; NEVER mutate or permit a re-execution.

    Applied receipts can precede later owner rotation; an older term is not a
    violation by itself. Staleness is checked against the receiver's term
    AT THE ATOMIC COMMIT BOUNDARY, not against a later witness snapshot.
    """
    rows = tuple(receipts)

    def deny(kind: Classification, reason: str) -> Decision:
        return Decision(kind, reason)

    if (not all(type(v) is str and v for v in (
            operation.domain, operation.operation_id,
            operation.effect_id, operation.owner))
            or type(operation.term) is not int or operation.term < 1):
        return deny(Classification.UNTRUSTED, "invalid-operation-identity")

    if not rows:
        return deny(Classification.UNKNOWN, "no-receiver-testimony")

    for row in rows:
        if not isinstance(row, ReceiverReceipt):
            return deny(Classification.UNTRUSTED, "malformed-receipt")
        if ((row.domain, row.operation_id, row.effect_id, row.owner, row.term)
            != (operation.domain, operation.operation_id,
                operation.effect_id, operation.owner, operation.term)):
            return deny(Classification.CONFLICT, "receipt-binding-mismatch")
        if (type(row.receipt_id) is not str or not row.receipt_id
            or type(row.term) is not int or row.term < 1
            or type(row.durable) is not bool or type(row.terminal) is not bool
            or (row.boundary_term is not None
                and (type(row.boundary_term) is not int or row.boundary_term < 1))
            or row.status not in (
                "applied", "aborted", "unknown", "not-seen", "rejected-stale")):
            return deny(Classification.UNTRUSTED, "malformed-receipt-fields")
        # A receiver's decision cannot be considered fenced without a
        # durable boundary watermark, irrespective of a claimed status.
        if not row.durable or row.boundary_term is None:
            return deny(Classification.UNTRUSTED, "missing-durable-atomic-boundary")
        if row.terminal and row.status not in ("applied", "aborted", "rejected-stale"):
            return deny(Classification.UNTRUSTED, "nonterminal-status-with-terminal-flag")
        if row.status == "applied" and not row.terminal:
            return deny(Classification.UNTRUSTED, "applied-without-terminal-receipt")

    # Duplicate transport copies of one immutable receipt are fine. One ID
    # with different contents indicates contradictory receiver testimony.
    by_id: dict[str, ReceiverReceipt] = {}
    for row in rows:
        prior = by_id.setdefault(row.receipt_id, row)
        if prior != row:
            return deny(Classification.CONFLICT, "same-receipt-id-diverged")
    unique = tuple(by_id.values())

    accepted = [r for r in unique if r.status == "applied"]
    aborted = [r for r in unique if r.status in ("aborted", "rejected-stale") and r.terminal]
    if any(r.boundary_term != r.term for r in accepted):
        # Fencing must match atomically at the receiver, both for stale
        # permits and for a forged future term presented before promotion.
        return deny(Classification.STALE_APPLIED, "effect-accepted-with-noncurrent-term")
    if len(accepted) > 1:
        return deny(Classification.CONFLICT, "multiple-distinct-application-receipts")
    if accepted and aborted:
        return deny(Classification.CONFLICT, "applied-and-terminal-rejected")
    if len(aborted) > 1 and len({r.status for r in aborted}) > 1:
        return deny(Classification.CONFLICT, "contradictory-terminal-rejections")
    if accepted:
        return deny(Classification.APPLIED, "durable-atomic-application-recorded")
    if aborted:
        return deny(Classification.ABORTED, "durable-terminal-no-effect-recorded")
    return deny(Classification.UNKNOWN, "no-terminal-receiver-decision")
