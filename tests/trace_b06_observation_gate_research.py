"""B06 observation-only transaction identity gate (research, NEVER signing).

The observer MUST see the exact broker-generated assistx metadata on the
original graph member and pinned boot. A sequence of 'transaction missing'
events is NOT sufficient release authority: a restart/partition/switch may
make an old transaction temporarily invisible. This module can only return
observation findings; it holds NO signer, etcd writer, or closure API.

Identity must be supplied independently by a trusted observer in future.
In the current disposable fixture it is NOT a server-signed generation.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import re

from trace_graph_entry_metadata import observed_exact

_TXID = re.compile(r"^neo4j-transaction-[0-9]+$")
META_KEYS = frozenset({
    "assistx_schema", "assistx_operation_id", "assistx_attempt_id",
    "assistx_reservation_id", "assistx_epoch", "assistx_term",
    "assistx_plan_id",
})


class ObservationRejected(ValueError):
    pass


@dataclass(frozen=True)
class PinnedGraphTarget:
    member: str
    boot: str
    database: str
    reservation_id: str
    metadata: dict[str, Any]
    broker_principal: str

    def __post_init__(self):
        if (not all(type(x) is str and 0 < len(x) <= 128
                    for x in (self.member, self.boot, self.reservation_id,
                              self.broker_principal))
                or self.database != "neo4j"
                or type(self.metadata) is not dict
                or set(self.metadata) != META_KEYS
                or self.metadata["assistx_schema"]
                != "research-gateway-entry-v1"
                or self.metadata["assistx_reservation_id"]
                != self.reservation_id
                or type(self.metadata["assistx_term"]) is not int
                or self.metadata["assistx_term"] < 1
                or not all(type(v) is str and 0 < len(v) <= 128
                           for k, v in self.metadata.items()
                           if k != "assistx_term")):
            raise ObservationRejected("INVALID_PINNED_TRANSACTION_METADATA")


@dataclass(frozen=True)
class GraphObservation:
    observer_principal: str
    member: str
    boot: str
    sequence: int
    rows: tuple[dict, ...]
    read_complete: bool = True

    def __post_init__(self):
        if (type(self.sequence) is not int or self.sequence < 1
                or type(self.rows) is not tuple
                or type(self.read_complete) is not bool):
            raise ObservationRejected("BAD_OBSERVER_SNAPSHOT")


@dataclass(frozen=True)
class ObservedActive:
    target: PinnedGraphTarget
    transaction_id: str
    observed_sequence: int
    signer_authorized: bool = False
    release_authorized: bool = False


@dataclass(frozen=True)
class ObservedAbsenceOnly:
    target: PinnedGraphTarget
    transaction_id: str
    observed_sequences: tuple[int, int]
    signer_authorized: bool = False
    release_authorized: bool = False


def _check(target: PinnedGraphTarget, observation: GraphObservation) -> None:
    if (observation.observer_principal == target.broker_principal
            or not observation.observer_principal
            or observation.member != target.member
            or observation.boot != target.boot):
        raise ObservationRejected("OBSERVER_IDENTITY_OR_GENERATION_CHANGED")
    if not observation.read_complete:
        raise ObservationRejected("OBSERVER_READ_UNCERTAIN")
    if not all(type(row) is dict and
               "transactionId" in row and "database" in row
               and "metaData" in row for row in observation.rows):
        raise ObservationRejected("OBSERVER_RESPONSE_INCOMPLETE")


def _matching_count(target: PinnedGraphTarget, rows: tuple[dict, ...]) -> int:
    # Count exact metadata matches; never take "first match wins".
    return sum(
        row.get("database") == target.database
        and row.get("metaData") == target.metadata
        and type(row.get("metaData")) is dict
        and type(row.get("transactionId")) is str
        and bool(_TXID.fullmatch(row["transactionId"]))
        for row in rows
    )


def capture_active(target: PinnedGraphTarget,
                   snapshot: GraphObservation) -> ObservedActive:
    _check(target, snapshot)
    if _matching_count(target, snapshot.rows) != 1:
        raise ObservationRejected("MISSING_OR_AMBIGUOUS_ORIGINAL_TRANSACTION")
    txid = observed_exact(snapshot.rows, target.metadata, target.database)
    if not txid:
        raise ObservationRejected("MISSING_OR_AMBIGUOUS_ORIGINAL_TRANSACTION")
    return ObservedActive(target, txid, snapshot.sequence)


def observe_absence_only(active: ObservedActive,
                         first: GraphObservation,
                         second: GraphObservation) -> ObservedAbsenceOnly:
    target = active.target
    _check(target, first)
    _check(target, second)
    if (not active.transaction_id
            or not _TXID.fullmatch(active.transaction_id)
            or first.observer_principal != second.observer_principal
            or first.sequence <= active.observed_sequence
            or second.sequence <= first.sequence):
        raise ObservationRejected("INVALID_OBSERVATION_SEQUENCE")
    if any(
            row.get("transactionId") == active.transaction_id
            for snap in (first, second) for row in snap.rows):
        raise ObservationRejected("ORIGINAL_TRANSACTION_STILL_VISIBLE")
    if any(_matching_count(target, snap.rows)
           for snap in (first, second)):
        raise ObservationRejected("EXACT_METADATA_STILL_ACTIVE_OR_REUSED")
    # Two independently *read* absences are not a signed physical closure.
    # Never return a receipt, signer capability, or Raft release operation.
    return ObservedAbsenceOnly(
        target, active.transaction_id, (first.sequence, second.sequence))
