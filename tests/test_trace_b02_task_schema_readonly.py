"""Offline negative tests for B02 live metadata-only preflight."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from probe_trace_b02_task_schema_readonly import (
    PreflightDenied, METADATA_QUERY, NODE_COUNT_QUERY,
    SAMPLE_QUERY, SAMPLE_LIMIT, _counts, read_only_preflight,
)


def test_realistic_sample_is_a_no_go_without_tenant_ownership():
    result=_counts([],31074,100,55)
    assert result["task_nodes_total"]==31074
    assert result["sample_count"]==100
    assert result["sample_payload_present"]==55
    assert result["ownership_property_keys_in_catalog"]==[]
    assert result["all_expected_ownership_keys_absent_from_catalog"] is True
    assert result["tenant_scoped_shadow_authorized"] is False
    assert result["global_operator_shadow_authorized"] is False
    assert result["backfill_authorized"] is False
    assert not result["raw_task_data_emitted"]


def test_even_all_property_names_present_is_not_an_authority_grant():
    result=_counts(["tenant_id","created_by","owner_user"],100,25,20)
    assert result["all_expected_ownership_keys_absent_from_catalog"] is False
    assert result["verdict"]=="HOLD_NO_AUTHORITATIVE_TASK_OWNERSHIP"
    assert not result["tenant_scoped_shadow_authorized"]


@pytest.mark.parametrize("found,total,sampled,payload",[
    ("tenant_id",100,20,1),
    (["tenant_id","not_authorized"],100,20,1),
    (["tenant_id","tenant_id"],100,20,1),
    ([],True,20,1),
    ([],100,True,1),
    ([],100,20,True),
    ([],100,101,3),
    ([],10,15,3),
    ([],100,12,15),
    ([],100,0,1),
    ([],100,0,-1),
    ([],0,1,0),
    ([],100,-1,0),
])
def test_inconsistent_or_untrusted_metadata_refused(found,total,sampled,payload):
    with pytest.raises(PreflightDenied):
        _counts(found,total,sampled,payload)


def test_no_opt_in_no_driver_access(monkeypatch):
    monkeypatch.delenv("ASSISTX_B02_SCHEMA_READONLY",raising=False)
    with pytest.raises(PreflightDenied,match="EXPLICIT_OPERATOR_OPT_IN_REQUIRED"):
        read_only_preflight()


def test_missing_existing_credentials_fail_before_driver(monkeypatch):
    monkeypatch.setenv("ASSISTX_B02_SCHEMA_READONLY","1")
    for name in ("NEO4J_URI","NEO4J_USER","NEO4J_PASSWORD","NEO4J_DATABASE"):
        monkeypatch.delenv(name,raising=False)
    with pytest.raises(PreflightDenied,match="EXISTING_NEO4J_RUNTIME_CONFIG_REQUIRED"):
        read_only_preflight()


def test_only_three_exact_fixed_read_queries_and_no_raw_fields():
    assert METADATA_QUERY.startswith("CALL db.propertyKeys()")
    assert NODE_COUNT_QUERY == "MATCH (t:Task) RETURN count(t) AS task_nodes_total"
    assert "WITH t LIMIT $limit" in SAMPLE_QUERY
    assert SAMPLE_LIMIT==100
    for statement in (METADATA_QUERY,NODE_COUNT_QUERY,SAMPLE_QUERY):
        assert not any(token in statement for token in
                       (" CREATE ", " DELETE ", " SET ", " MERGE ", " CALL dbms",
                        "RETURN t ", "RETURN t.", "RETURN properties("))
    for forbidden in ("t.title", "t.id", "t.summary", "t.last_error",
                      "t.secret", "RETURN t AS", "RETURN t.payload_json"):
        assert all(forbidden not in q for q in
                   (METADATA_QUERY,NODE_COUNT_QUERY,SAMPLE_QUERY))


def test_report_never_contains_sensitive_graph_properties():
    report=json.dumps(_counts([],31074,100,55),sort_keys=True)
    for marker in ("neo4j://","bolt://","password","task_id",
                   "title","api_key","secret_token"):
        assert marker not in report


def test_module_is_read_only_and_no_live_service_mutation():
    module=Path(__file__).with_name("probe_trace_b02_task_schema_readonly.py").read_text()
    for code in ("docker run", "docker exec", "DROP DATABASE",
                 "session.execute_write", "write_transaction(",
                 "session.run('CREATE", "UPDATE t", "os.system("):
        assert code not in module
    assert 'default_access_mode=READ_ACCESS' in module
    assert 'connection_timeout=2' in module
    assert 'timeout=3' in module
