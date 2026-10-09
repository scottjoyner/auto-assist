"""Research-only fail-closed graph entry boundary; no production route wiring.

This module does not grant Neo4j permissions or implement distributed fencing.
Physical enforcement additionally requires a network/credential isolation gate.
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4


class AdmissionDenied(RuntimeError):
    """No graph transaction is authorized."""


@dataclass(frozen=True)
class Grant:
    reservation_id: str
    token: str
    term: int
    epoch: str


@dataclass(frozen=True)
class BoundAttempt:
    operation_id: str
    attempt_id: str
    grant: Grant


class Authority(Protocol):
    def admit(self, operation_id: str) -> Grant | None: ...


class Journal(Protocol):
    def append(self, kind: str, attempt: BoundAttempt) -> None: ...


class Graph(Protocol):
    def execute(self, attempt: BoundAttempt, query: str) -> object: ...


class ResearchGraphEntry:
    """Single-attempt gateway: never releases uncertain reservations.

    The application does not accept grants, tokens, terms or operation IDs from
    workers.  Only the independent verifier can release a reservation.
    """

    def __init__(self, authority: Authority, journal: Journal, graph: Graph):
        self._authority = authority
        self._journal = journal
        self._graph = graph

    def execute(self, query: str) -> object:
        if not isinstance(query, str) or not query.strip():
            raise AdmissionDenied("INVALID_QUERY")
        operation_id = uuid4().hex
        try:
            grant = self._authority.admit(operation_id)
        except Exception as exc:
            raise AdmissionDenied("AUTHORITY_UNAVAILABLE") from exc
        if grant is None:
            raise AdmissionDenied("CAPACITY_DENIED")
        # Reject incomplete grants without releasing occupied capacity.
        if not (grant.reservation_id and grant.token and grant.epoch
                and type(grant.term) is int and grant.term > 0):
            raise AdmissionDenied("INVALID_AUTHORITY_GRANT")
        attempt = BoundAttempt(operation_id, uuid4().hex, grant)
        try:
            self._journal.append("ADMISSION_COMMITTED", attempt)
        except Exception as exc:
            raise AdmissionDenied("AUDIT_UNAVAILABLE_RESERVATION_HELD") from exc
        try:
            self._journal.append("GRAPH_ENTRY_INTENT", attempt)
        except Exception as exc:
            raise AdmissionDenied("AUDIT_UNAVAILABLE_RESERVATION_HELD") from exc
        try:
            return self._graph.execute(attempt, query)
        except Exception as exc:
            # Missing or failed response is not evidence that the physical tx ended.
            try:
                self._journal.append("CLOSURE_UNCERTAIN", attempt)
            except Exception:
                pass
            raise AdmissionDenied("GRAPH_RESULT_UNCERTAIN_RESERVATION_HELD") from exc
