"""Offline negative tests: no Neo4j, PostgreSQL, secrets or Docker."""
import pytest
from trace_graph_entry_research import AdmissionDenied, Grant, ResearchGraphEntry


class Authority:
    def __init__(self, grant=None, error=None):
        self.grant, self.error, self.requests = grant, error, []
    def admit(self, operation_id):
        self.requests.append(operation_id)
        if self.error:
            raise self.error
        return self.grant


class Journal:
    def __init__(self, fail=None):
        self.events, self.fail = [], fail
    def append(self, kind, attempt):
        if kind == self.fail:
            raise OSError("audit down")
        self.events.append((kind, attempt))


class Graph:
    def __init__(self, error=None):
        self.attempts, self.error = [], error
    def execute(self, attempt, query):
        self.attempts.append((attempt, query))
        if self.error:
            raise self.error
        return {"ok": True}


def rig(grant=None, audit_fail=None, graph_error=None, authority_error=None):
    a = Authority(grant, authority_error)
    j = Journal(audit_fail)
    g = Graph(graph_error)
    return ResearchGraphEntry(a, j, g), a, j, g


VALID = Grant("reservation-1", "opaque-token", 1, "epoch-1")


@pytest.mark.parametrize("query", ["", " ", None])
def test_invalid_query_never_calls_admission(query):
    entry, a, j, g = rig(VALID)
    with pytest.raises(AdmissionDenied, match="INVALID_QUERY"):
        entry.execute(query)
    assert not a.requests and not j.events and not g.attempts


@pytest.mark.parametrize("grant,error,reason", [
    (None, None, "CAPACITY_DENIED"),
    (None, ConnectionError("pg down"), "AUTHORITY_UNAVAILABLE"),
    (Grant("", "x", 1, "e"), None, "INVALID_AUTHORITY_GRANT"),
    (Grant("r", "x", 0, "e"), None, "INVALID_AUTHORITY_GRANT"),
    (Grant("r", "x", True, "e"), None, "INVALID_AUTHORITY_GRANT"),
])
def test_no_graph_entry_without_valid_grant(grant, error, reason):
    entry, a, j, g = rig(grant, authority_error=error)
    with pytest.raises(AdmissionDenied, match=reason):
        entry.execute("RETURN 1")
    assert not g.attempts


@pytest.mark.parametrize("event", ["ADMISSION_COMMITTED", "GRAPH_ENTRY_INTENT"])
def test_audit_failure_denies_graph_but_keeps_reservation(event):
    entry, a, j, g = rig(VALID, audit_fail=event)
    with pytest.raises(AdmissionDenied, match="RESERVATION_HELD"):
        entry.execute("RETURN 1")
    assert not g.attempts
    assert not hasattr(entry, "release")


def test_success_uses_gateway_owned_identifiers():
    entry, a, j, g = rig(VALID)
    assert entry.execute("RETURN 1") == {"ok": True}
    attempt, query = g.attempts[0]
    assert query == "RETURN 1"
    assert attempt.operation_id == a.requests[0]
    assert attempt.attempt_id != attempt.operation_id
    assert attempt.grant == VALID
    assert [kind for kind, _ in j.events] == ["ADMISSION_COMMITTED", "GRAPH_ENTRY_INTENT"]
    assert not hasattr(entry, "release")


def test_graph_error_quarantines_without_release():
    entry, a, j, g = rig(VALID, graph_error=TimeoutError("blackhole"))
    with pytest.raises(AdmissionDenied, match="RESERVATION_HELD"):
        entry.execute("RETURN 1")
    assert [kind for kind, _ in j.events][-1] == "CLOSURE_UNCERTAIN"
    assert not hasattr(entry, "release")


def test_distinct_calls_never_reuse_operation_or_attempt_ids():
    entry, a, j, g = rig(VALID)
    entry.execute("RETURN 1")
    entry.execute("RETURN 2")
    x, y = [pair[0] for pair in g.attempts]
    assert x.operation_id != y.operation_id
    assert x.attempt_id != y.attempt_id
