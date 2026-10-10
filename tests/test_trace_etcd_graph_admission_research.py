"""Offline quorum-to-graph admission seam tests; no physical Neo4j claims."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from test_trace_etcd_quorum_fence_research import FakeEtcd
from trace_etcd_quorum_fence_research import (
    EtcdQuorumFence, FenceRefused, canon, takeover_request
)
from trace_etcd_graph_admission_research import QuorumPlanGrantAdapter
from trace_graph_entry_guarded import ProtectedGraphEntry, QueryPlan
from trace_graph_entry_research import AdmissionDenied

GENESIS = "quorum-gateway-fixture-20261010"


class Graph:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def execute(self, attempt, cypher, params):
        self.calls.append((attempt, cypher, dict(params)))
        if self.error:
            raise self.error
        return {"value": params["value"]}


class Journal:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    def append(self, kind, attempt, plan_id):
        if self.fail:
            raise OSError("DISPOSABLE_AUDIT_FULL")
        self.events.append((kind, attempt, plan_id))


def setup(fail_audit=False, graph_error=None):
    store = FakeEtcd()
    operator = Ed25519PrivateKey.generate()
    witness = Ed25519PrivateKey.generate()
    pub = lambda key: key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    quorum = EtcdQuorumFence(
        store, "/assistx/research/fencing/gateway-unit")
    quorum.bootstrap(GENESIS, "gateway-1", pub(operator), pub(witness), 1)
    auth = QuorumPlanGrantAdapter(
        quorum, "gateway-1", 1, GENESIS)
    journal = Journal(fail_audit)
    graph = Graph(graph_error)
    entry = ProtectedGraphEntry(
        {"approved": QueryPlan("RETURN $value AS value", ("value",))},
        auth, journal, graph, GENESIS)
    return entry, quorum, store, operator, graph, journal


def test_real_cas_reservation_precedes_graph_and_is_not_autoreleased():
    entry, quorum, store, operator, graph, journal = setup()
    assert entry.execute("approved", {"value": 42}) == {"value": 42}
    assert len(graph.calls) == 1
    attempt = graph.calls[0][0]
    view = quorum.snapshot()
    assert attempt.grant.token in view.document["pending"]
    assert view.document["pending"][attempt.grant.token]["operation"] == (
        "approved:" + attempt.operation_id)
    assert [kind for kind, _, _ in journal.events] == [
        "ADMISSION_COMMITTED", "GRAPH_ENTRY_INTENT", "GRAPH_CALL_RETURNED"]
    assert not hasattr(entry, "release") and not hasattr(
        QuorumPlanGrantAdapter, "release")
    with pytest.raises(AdmissionDenied, match="AUTHORITY_UNAVAILABLE"):
        entry.execute("approved", {"value": 43})
    approval = takeover_request(quorum.snapshot(), "gateway-2")
    with pytest.raises(FenceRefused, match="UNCERTAIN_PHYSICAL_WORK"):
        quorum.takeover("gateway-2", approval, operator.sign(canon(approval)))


def test_unregistered_worker_cypher_denied_before_quorum_write():
    entry, quorum, store, operator, graph, journal = setup()
    with pytest.raises(AdmissionDenied, match="UNREGISTERED_QUERY_PLAN"):
        entry.execute("RETURN 42", {})
    assert not quorum.snapshot().document["pending"]
    assert not graph.calls


def test_graph_failure_still_holds_consensus_reservation():
    entry, quorum, store, operator, graph, journal = setup(
        graph_error=TimeoutError("synthetic Bolt blackout"))
    with pytest.raises(AdmissionDenied, match="RESERVATION_HELD"):
        entry.execute("approved", {"value": 1})
    assert len(quorum.snapshot().document["pending"]) == 1
    assert journal.events[-1][0] == "CLOSURE_UNCERTAIN"


def test_audit_failure_still_holds_consensus_reservation():
    entry, quorum, store, operator, graph, journal = setup(fail_audit=True)
    with pytest.raises(AdmissionDenied, match="AUDIT_UNAVAILABLE_RESERVATION_HELD"):
        entry.execute("approved", {"value": 1})
    assert len(quorum.snapshot().document["pending"]) == 1
    assert graph.calls == []


def test_stale_gateway_owner_denied_after_signed_clean_takeover():
    entry, quorum, store, operator, graph, journal = setup()
    request = takeover_request(quorum.snapshot(), "gateway-2")
    assert quorum.takeover("gateway-2", request, operator.sign(canon(request))) == 2
    with pytest.raises(AdmissionDenied, match="AUTHORITY_UNAVAILABLE"):
        entry.execute("approved", {"value": 1})
    assert not quorum.snapshot().document["pending"]
    assert not graph.calls


def test_quorum_loss_denies_graph_without_local_fallback():
    entry, quorum, store, operator, graph, journal = setup()
    store.fail = True
    with pytest.raises(AdmissionDenied, match="AUTHORITY_UNAVAILABLE"):
        entry.execute("approved", {"value": 1})
    assert not graph.calls


def test_require_pinned_cluster_and_term_at_startup():
    entry, quorum, store, operator, graph, journal = setup()
    with pytest.raises(ValueError, match="INVALID_PINNED"):
        QuorumPlanGrantAdapter(quorum, "gateway-1", 0, GENESIS)
    with pytest.raises(ValueError, match="REQUIRE_PINNED"):
        QuorumPlanGrantAdapter(
            EtcdQuorumFence(store, quorum.key), "gateway-1", 1, GENESIS)
