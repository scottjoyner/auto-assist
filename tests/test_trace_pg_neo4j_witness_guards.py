"""Offline denial tests for separate-process PG + Neo4j witness probe.

These tests must never start Docker, connect to Neo4j, or load credentials.
"""
import pytest

import probe_trace_pg_neo4j_witness_custody as witness


def tx(txid, text, database="neo4j"):
    return {"transactionId":txid,"currentQuery":text,"database":database}


def test_explicit_research_opt_in_precedes_inspection(monkeypatch):
    monkeypatch.delenv("ASSISTX_TRACE_PG_NEO4J_WITNESS_RESEARCH",raising=False)
    monkeypatch.setattr(witness,"_address",
        lambda: (_ for _ in ()).throw(AssertionError("no Docker before opt-in")))
    with pytest.raises(RuntimeError,match="EXPLICIT_WITNESS_PROBE_OPT_IN_REQUIRED"):
        witness.run()


def test_observer_requires_exact_neo4j_transaction_id_and_token():
    token="a"*32
    query="UNWIND range(1,20) AS n RETURN n /* "+witness.MARKER+"_"+token+" */"
    assert witness._observed_exact([tx("neo4j-transaction-21",query)],token)=="neo4j-transaction-21"
    assert witness._observed_exact([tx("neo4j-transaction-21",query)],"b"*32) is None
    assert witness._observed_exact([tx("neo4j-transaction-21",query,"system")],token) is None
    assert witness._observed_exact([tx("neo4j-transaction-invalid",query)],token) is None


def test_ambiguous_transaction_evidence_must_not_bind_token():
    token="c"*32
    query="UNWIND range(1,20) AS n RETURN n /* "+witness.MARKER+"_"+token+" */"
    assert witness._observed_exact([
        tx("neo4j-transaction-41",query),
        tx("neo4j-transaction-42",query)],token) is None


def test_missing_query_and_nonquery_text_deny_binding():
    token="f"*32
    assert witness._observed_exact([],token) is None
    assert witness._observed_exact([
        tx("neo4j-transaction-21","RETURN '"+
           witness.MARKER+"_"+token+"' AS untrusted")],token) is None


def test_all_outputs_keep_production_and_distributed_authority_false():
    source=open(witness.__file__,encoding="utf-8").read()
    assert '"global_quorum_failover_proven":False' in source
    assert '"neo4j_server_enforced_token_binding":False' in source
    assert '"production_authority":False' in source
    assert 'if worker and worker.is_alive()' in source
    assert 'if witness and witness.is_alive()' in source
