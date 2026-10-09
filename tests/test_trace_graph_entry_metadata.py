"""Offline exact server-metadata matching negatives."""
from trace_graph_entry_metadata import gateway_metadata, observed_exact
from trace_graph_entry_research import BoundAttempt, Grant

ATTEMPT = BoundAttempt(
    "gateway-op-1", "gateway-attempt-1",
    Grant("reservation-1", "unlogged-secret-token", 1, "epoch-1")
)


def row(txid, metadata, db="neo4j"):
    return {"transactionId": txid, "database": db,
            "metaData": metadata, "currentQuery": "RETURN 1"}


def test_metadata_never_carries_secret_or_raw_cypher():
    m = gateway_metadata(ATTEMPT, "bounded_read")
    assert m["assistx_attempt_id"] == ATTEMPT.attempt_id
    assert m["assistx_epoch"] == ATTEMPT.grant.epoch
    assert all("token" not in k for k in m)
    assert "unlogged-secret-token" not in repr(m)
    assert "RETURN 1" not in repr(m)


def test_exact_meta_binding_does_not_depend_on_query_comments():
    m = gateway_metadata(ATTEMPT, "bounded_read")
    rows = [row("neo4j-transaction-7", m)]
    assert observed_exact(rows, m) == "neo4j-transaction-7"
    assert observed_exact(rows, dict(m, assistx_attempt_id="forged")) is None
    assert observed_exact([row("neo4j-transaction-7", m, "system")], m) is None


def test_ambiguous_missing_malformed_transactions_fail_closed():
    m = gateway_metadata(ATTEMPT, "bounded_read")
    assert observed_exact([], m) is None
    assert observed_exact([
        row("neo4j-transaction-7", m), row("neo4j-transaction-8", m)
    ], m) is None
    assert observed_exact([row("neo4j-transaction-X", m)], m) is None
    assert observed_exact([row("neo4j-transaction-7", None)], m) is None
    assert observed_exact([row("neo4j-transaction-7", m)], {}) is None
    assert observed_exact([row("neo4j-transaction-7", m)], m, "system") is None


def test_string_substrings_and_boolean_term_cannot_match():
    m = gateway_metadata(ATTEMPT, "bounded_read")
    mismatched = dict(m, assistx_attempt_id="prefix" + ATTEMPT.attempt_id)
    assert observed_exact([row("neo4j-transaction-7", mismatched)], m) is None
    mismatched = dict(m, assistx_term=True)
    assert observed_exact([row("neo4j-transaction-7", mismatched)], m) is None
