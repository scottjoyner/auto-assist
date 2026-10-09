"""Draft #148 research: trusted-plan graph entry, never a worker Cypher proxy.

NO production wiring, no server-enforced Neo4j isolation, no failover claim.
A trusted deployment MUST separately restrict Bolt networking and Neo4j roles.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol
from uuid import uuid4

from trace_graph_entry_research import AdmissionDenied, BoundAttempt, Grant

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class QueryPlan:
    """Source-reviewed static Cypher; read-only is enforced by Neo4j role."""
    cypher: str
    parameters: tuple[str, ...] = ()
    max_parameter_bytes: int = 4096

    def __post_init__(self) -> None:
        if not self.cypher.strip() or self.max_parameter_bytes < 2:
            raise ValueError("INVALID_PLAN")
        if len(set(self.parameters)) != len(self.parameters):
            raise ValueError("DUPLICATE_PARAMETER")
        if not all(_KEY.fullmatch(key) for key in self.parameters):
            raise ValueError("INVALID_PARAMETER_KEY")


class PlanAuthority(Protocol):
    def admit(self, operation_id: str, plan_id: str) -> Grant | None: ...


class EntryJournal(Protocol):
    def append(self, kind: str, attempt: BoundAttempt, plan_id: str) -> None: ...


class PlanGraph(Protocol):
    def execute(
        self, attempt: BoundAttempt, cypher: str, parameters: Mapping[str, object]
    ) -> object: ...


class ProtectedGraphEntry:
    """A strict synchronous seam. Only the independent witness may release.

    A successful driver response is NOT an independent Neo4j closure proof.
    """

    def __init__(
        self,
        plans: Mapping[str, QueryPlan],
        authority: PlanAuthority,
        journal: EntryJournal,
        graph: PlanGraph,
        expected_epoch: str,
    ) -> None:
        if not expected_epoch or not plans or not all(
            _KEY.fullmatch(key) and isinstance(plan, QueryPlan)
            for key, plan in plans.items()
        ):
            raise ValueError("INVALID_GATEWAY_POLICY")
        self._plans = MappingProxyType(dict(plans))
        self._authority = authority
        self._journal = journal
        self._graph = graph
        self._expected_epoch = expected_epoch

    def execute(self, plan_id: str, parameters: Mapping[str, object]) -> object:
        if plan_id not in self._plans:
            raise AdmissionDenied("UNREGISTERED_QUERY_PLAN")
        plan = self._plans[plan_id]
        if not isinstance(parameters, Mapping) or set(parameters) != set(plan.parameters):
            raise AdmissionDenied("INVALID_QUERY_PARAMETERS")
        if any(type(value) not in (str, int, float, bool, type(None))
               or (isinstance(value, str) and len(value) > 2048)
               for value in parameters.values()):
            raise AdmissionDenied("INVALID_QUERY_PARAMETERS")
        # Serialization both bounds and snapshots worker data before admission.
        try:
            encoded = json.dumps(dict(parameters), sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
            if len(encoded.encode("utf-8")) > plan.max_parameter_bytes:
                raise ValueError("too large")
            frozen_parameters = MappingProxyType(json.loads(encoded))
        except (ValueError, TypeError, OverflowError) as exc:
            raise AdmissionDenied("INVALID_QUERY_PARAMETERS") from exc
        operation_id = uuid4().hex
        try:
            grant = self._authority.admit(operation_id, plan_id)
        except Exception as exc:
            raise AdmissionDenied("AUTHORITY_UNAVAILABLE") from exc
        if grant is None:
            raise AdmissionDenied("CAPACITY_DENIED")
        if not (isinstance(grant, Grant) and grant.epoch == self._expected_epoch
                and grant.reservation_id and grant.token
                and type(grant.term) is int and grant.term > 0):
            # A grant may have consumed a slot; never optimistically release.
            raise AdmissionDenied("AUTHORITY_GRANT_UNTRUSTED_RESERVATION_HELD")
        attempt = BoundAttempt(operation_id, uuid4().hex, grant)
        for kind in ("ADMISSION_COMMITTED", "GRAPH_ENTRY_INTENT"):
            try:
                self._journal.append(kind, attempt, plan_id)
            except Exception as exc:
                raise AdmissionDenied("AUDIT_UNAVAILABLE_RESERVATION_HELD") from exc
        try:
            result = self._graph.execute(attempt, plan.cypher, frozen_parameters)
        except BaseException as exc:
            # Cancellation, KeyboardInterrupt and SystemExit also leave state
            # unknown. This is best effort; actual custody is external.
            try:
                self._journal.append("CLOSURE_UNCERTAIN", attempt, plan_id)
            except BaseException:
                pass
            if not isinstance(exc, Exception):
                raise
            raise AdmissionDenied("GRAPH_RESULT_UNCERTAIN_RESERVATION_HELD") from exc
        try:
            self._journal.append("GRAPH_CALL_RETURNED", attempt, plan_id)
        except Exception as exc:
            raise AdmissionDenied("AUDIT_UNAVAILABLE_RESERVATION_HELD") from exc
        return result
