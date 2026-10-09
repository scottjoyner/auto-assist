"""Offline lifecycle and durable-journal negative tests; no Docker/PG/Neo4j."""
import sqlite3
from dataclasses import dataclass

import pytest

from trace_graph_entry_guarded import ProtectedGraphEntry, QueryPlan
from trace_graph_entry_research import AdmissionDenied, BoundAttempt, Grant
from trace_graph_entry_research_journal import (
    LedgerIntegrityError, SqliteResearchJournal, ZERO
)

EPOCH = "research-epoch-1"
GRANT = Grant("reservation-1", "PRIVATE-MUST-NOT-APPEAR", 7, EPOCH)
PLAN = QueryPlan("RETURN $value AS result", ("value",), max_parameter_bytes=64)


class Authority:
    def __init__(self, grant=GRANT, error=None):
        self.grant, self.error, self.calls = grant, error, []
    def admit(self, operation_id, plan_id):
        self.calls.append((operation_id, plan_id))
        if self.error:
            raise self.error
        return self.grant


class Graph:
    def __init__(self, error=None):
        self.calls, self.error = [], error
    def execute(self, attempt, cypher, params):
        self.calls.append((attempt, cypher, params))
        if self.error:
            raise self.error
        return {"value": params["value"]}


class Journal:
    def __init__(self, fail=None):
        self.events, self.fail = [], fail
    def append(self, kind, attempt, plan_id):
        if kind == self.fail:
            raise OSError("audit offline")
        self.events.append((kind, attempt, plan_id))


def setup(grant=GRANT, fail=None, error=None, authority_error=None, journal=None):
    authority = Authority(grant, authority_error)
    graph = Graph(error)
    journal = journal or Journal(fail)
    entry = ProtectedGraphEntry(
        {"approved_read": PLAN}, authority, journal, graph, EPOCH
    )
    return entry, authority, journal, graph


def test_success_uses_only_allowlisted_static_cypher():
    entry, authority, journal, graph = setup()
    assert entry.execute("approved_read", {"value": 3}) == {"value": 3}
    assert len(authority.calls) == 1
    assert graph.calls[0][1] == PLAN.cypher
    assert dict(graph.calls[0][2]) == {"value": 3}
    assert [event[0] for event in journal.events] == [
        "ADMISSION_COMMITTED", "GRAPH_ENTRY_INTENT", "GRAPH_CALL_RETURNED"
    ]
    assert not hasattr(entry, "release")


@pytest.mark.parametrize("plan_id,params", [
    ("RETURN 1", {}), ("unknown", {}), ([], {}), ("approved_read", {}),
    ("approved_read", {"value": 1, "extra": 2}),
    ("approved_read", {"value": {"nested": "untrusted"}}),
    ("approved_read", {"value": float("nan")}),
    ("approved_read", {"value": "x" * 200}),
    ("approved_read", "value=1"),
])
def test_bad_plan_or_parameters_never_consume_admission(plan_id, params):
    entry, authority, journal, graph = setup()
    with pytest.raises(AdmissionDenied):
        entry.execute(plan_id, params)
    assert not authority.calls and not graph.calls


@pytest.mark.parametrize("grant,authority_error", [
    (None, None),
    (GRANT, ConnectionError("pg unavailable")),
    (Grant("r", "t", 7, "stale-epoch"), None),
    (Grant("r", "t", 0, EPOCH), None),
    (Grant("r", "t", True, EPOCH), None),
])
def test_invalid_authority_is_fail_closed(grant, authority_error):
    entry, _, journal, graph = setup(grant, authority_error=authority_error)
    with pytest.raises(AdmissionDenied):
        entry.execute("approved_read", {"value": 1})
    assert graph.calls == []


@pytest.mark.parametrize("event", ["ADMISSION_COMMITTED", "GRAPH_ENTRY_INTENT",
                                    "GRAPH_CALL_RETURNED"])
def test_audit_failure_never_optimistically_releases(event):
    entry, _, journal, graph = setup(fail=event)
    with pytest.raises(AdmissionDenied, match="RESERVATION_HELD"):
        entry.execute("approved_read", {"value": 1})
    assert not hasattr(entry, "release")
    assert len(graph.calls) == (1 if event == "GRAPH_CALL_RETURNED" else 0)


@pytest.mark.parametrize("error", [TimeoutError("bolt"), KeyboardInterrupt()])
def test_query_interruption_records_uncertainty(error):
    entry, _, journal, graph = setup(error=error)
    with pytest.raises((AdmissionDenied, KeyboardInterrupt)):
        entry.execute("approved_read", {"value": 1})
    assert journal.events[-1][0] == "CLOSURE_UNCERTAIN"
    assert len(graph.calls) == 1


def test_reopen_persistent_ledger_validates_hash_chain_and_redaction(tmp_path):
    path = tmp_path / "trace-research.sqlite"
    ledger = SqliteResearchJournal(path)
    entry, authority, journal, graph = setup(journal=ledger)
    assert entry.execute("approved_read", {"value": "secret input"}) == {
        "value": "secret input"
    }
    reopened = SqliteResearchJournal(path)
    count, tip = reopened.verify_chain()
    assert count == 3 and tip != ZERO
    with sqlite3.connect(path) as db:
        joined = " ".join(row[0] for row in db.execute(
            "SELECT payload FROM graph_entry_events ORDER BY seq"))
    assert "PRIVATE-MUST-NOT-APPEAR" not in joined
    assert "secret input" not in joined
    assert "RETURN $value" not in joined


def test_ledger_refuses_invalid_transition_and_replay(tmp_path):
    ledger = SqliteResearchJournal(tmp_path / "events.sqlite")
    attempt = BoundAttempt("op-1", "attempt-1", GRANT)
    with pytest.raises(LedgerIntegrityError, match="INVALID_EVENT_TRANSITION"):
        ledger.append("GRAPH_ENTRY_INTENT", attempt, "approved_read")
    ledger.append("ADMISSION_COMMITTED", attempt, "approved_read")
    with pytest.raises(LedgerIntegrityError, match="INVALID_EVENT_TRANSITION"):
        ledger.append("ADMISSION_COMMITTED", attempt, "approved_read")
    with pytest.raises(LedgerIntegrityError, match="EVENT_IDENTITY_CHANGED"):
        ledger.append("GRAPH_ENTRY_INTENT",
                      BoundAttempt("op-2", "attempt-1", GRANT), "approved_read")
    ledger.append("GRAPH_ENTRY_INTENT", attempt, "approved_read")
    ledger.append("CLOSURE_UNCERTAIN", attempt, "approved_read")
    with pytest.raises(LedgerIntegrityError, match="INVALID_EVENT_TRANSITION"):
        ledger.append("GRAPH_CALL_RETURNED", attempt, "approved_read")
    assert ledger.verify_chain()[0] == 3


def test_ledger_detects_mutated_event_without_rehashed_chain(tmp_path):
    path = tmp_path / "events.sqlite"
    ledger = SqliteResearchJournal(path)
    ledger.append("ADMISSION_COMMITTED",
                  BoundAttempt("op-1", "attempt-1", GRANT), "approved_read")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE graph_entry_events SET payload=? WHERE seq=1",
                   ('{"tampered":true}',))
    with pytest.raises(LedgerIntegrityError, match="BROKEN_LEDGER_CHAIN"):
        ledger.verify_chain()


def test_ledger_audit_error_prevents_graph_call(tmp_path):
    class RefusingJournal:
        def append(self, kind, attempt, plan_id):
            raise sqlite3.OperationalError("disk full")

    entry, _, _, graph = setup(journal=RefusingJournal())
    with pytest.raises(AdmissionDenied, match="AUDIT_UNAVAILABLE"):
        entry.execute("approved_read", {"value": 3})
    assert not graph.calls
