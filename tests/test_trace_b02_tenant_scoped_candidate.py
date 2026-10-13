"""Synthetic-only tenant scope and minimal-projection negative acceptance."""
from pathlib import Path
from fastapi.security import HTTPBasicCredentials
import pytest
from trace_b02_tenant_scoped_candidate import (
    TenantDenied, TenantContext, PLAN_ID, TENANT_CYPHER, PROJECTION_KEYS,
    authorize_fixture, tenant_params, safe_tenant_rows, disposition)

def fixture_auth(**kw):
    p=dict(principal="tenant-a-operator",
           credentials=HTTPBasicCredentials(username="tenant-a-operator",
                                           password="synthetic-password"),
           configured_user="tenant-a-operator",
           configured_password="synthetic-password",
           tenant_bindings={"tenant-a-operator":"tenant-a"},
           explicit_offline=True)
    p.update(kw)
    return authorize_fixture(**p)

def row(tenant="tenant-a"):
    return dict(tenant_id=tenant, id="fixture-1", kind="harness_reflect",
                status="READY", target_agent_id="agent-1",updated_at_ts=7)

def test_fixed_plan_and_verified_fixture_binding():
    c=fixture_auth()
    assert c==TenantContext("tenant-a-operator","tenant-a")
    assert tenant_params(c,status="READY")=={
        "status":"READY","limit":40,"tenant_id":"tenant-a"}
    assert PLAN_ID!="harness_evolution_tasks_read_v1"
    assert "tenant_id: $tenant_id" in TENANT_CYPHER
    assert "status: $status" in TENANT_CYPHER
    assert "LIMIT $limit" in TENANT_CYPHER
    assert "RETURN t { .tenant_id" in TENANT_CYPHER
def test_query_does_not_fetch_free_text_or_payloads():
    for secret_field in ("title","summary","last_error",
                         "payload_json","secret_token","endpoint","fail_set"):
        assert "."+secret_field not in TENANT_CYPHER
        assert secret_field not in PROJECTION_KEYS

@pytest.mark.parametrize("change",[
    {"principal":"forged-header-principal"},
    {"credentials":HTTPBasicCredentials(username="tenant-a-operator",password="bad")},
    {"credentials":HTTPBasicCredentials(username="other",password="synthetic-password")},
    {"tenant_bindings":{}},
    {"tenant_bindings":{"tenant-a-operator":None}},
    {"tenant_bindings":{"tenant-a-operator":"tenant a"}},
    {"tenant_bindings":{"tenant-a-operator":"tenant-a'} MATCH (n)"}},
    {"explicit_offline":False},
])
def test_missing_invalid_or_forged_identity_fails_closed(change):
    with pytest.raises(TenantDenied):
        fixture_auth(**change)

def test_params_fail_closed_on_unreviewed_status_and_limit():
    with pytest.raises(ValueError):
        tenant_params(fixture_auth(),status="ANY")
    with pytest.raises(ValueError):
        tenant_params(fixture_auth(),status="READY",limit=41)

def test_restricted_response_contains_only_fixed_safe_fields():
    result=safe_tenant_rows(fixture_auth(),[row()])
    assert result==[dict(id="fixture-1", kind="harness_reflect",
                        title=None, status="READY", target_agent_id="agent-1",
                        payload_json=None, updated_at_ts=7,
                        summary=None,last_error=None)]
@pytest.mark.parametrize("rows",[
    [row("tenant-b")],
    [row(),row("tenant-b")],
    [row(None)],
    [dict(row(),tenant_id="")],
    [dict((k,v) for k,v in row().items() if k!="tenant_id")],
    [dict(row(),payload_json="SYNTHETIC-PRIVATE")],
    [dict(row(),title="SYNTHETIC-PRIVATE")],
    [dict(row(),extra="SYNTHETIC-PRIVATE")],
    [dict(row(),status="FAILED")],
    [dict(row(),kind="other_task_kind")],
    [dict(row(),id="private@email.example")],
    [dict(row(),target_agent_id="private@email.example")],
    [row()]*41,
])
def test_cross_scope_unattributed_unsafely_enriched_results_fail_whole(rows):
    with pytest.raises(TenantDenied):
        safe_tenant_rows(fixture_auth(),rows)

def test_approved_status_param_must_match_each_row():
    with pytest.raises(TenantDenied):
        safe_tenant_rows(fixture_auth(),[row()],expected_status="FAILED")

def test_no_runtime_connectors_or_release_authorization():
    d=disposition()
    for x in ("real_tenant_source_attested","full_view_parity_proven",
              "graph_network_exclusivity_proven","production_route_authorized",
              "release_authorized"):
        assert d[x] is False
    assert d["raw_payload_and_free_text_not_queried"] is True
    content=Path(__file__).with_name("trace_b02_tenant_scoped_candidate.py").read_text()
    for unsafe in ("GraphDatabase.driver","import neo4j","docker","app.get(",
                   "app.post(","os.getenv(","subprocess"):
        assert unsafe not in content