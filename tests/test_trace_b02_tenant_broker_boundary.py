"""Synthetic-only B02 caller-to-tenant binding and source-schema blockers."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.security import HTTPBasicCredentials
import pytest

from trace_b02_tenant_broker_boundary import (
    BrokerBoundaryDenied, ServerOwnedDirectory, resolve_scoped_read,
    release_decision,
)
from trace_b02_harness_projection_candidate import TENANT_PLAN_ID, CYPHER_TENANT_SCOPED


def inputs():
    return dict(
        principal="synthetic-owner",
        credentials=HTTPBasicCredentials(username="synthetic-owner", password="fixture-secret"),
        configured_user="synthetic-owner",
        configured_password="fixture-secret",
        allowed_basic_operators="synthetic-owner",
        server_owned_directory=ServerOwnedDirectory(
            revision="fixture-in-memory-1",
            principal_tenants={"synthetic-owner": "synthetic-tenant-a",
                               "synthetic-unknown": "synthetic-tenant-b"},
        ),
        request={"plan_id": TENANT_PLAN_ID, "status": "READY"},
    )


def resolved(**changes):
    data=inputs()
    data.update(changes)
    return resolve_scoped_read(**data)


def test_minimal_verified_identity_uses_only_server_owned_tenant():
    result=resolved()
    assert result.plan_id==TENANT_PLAN_ID
    assert result.cypher==CYPHER_TENANT_SCOPED
    assert result.parameters == {
        "status":"READY","limit":40,"tenant_id":"synthetic-tenant-a"}
    assert result.principal=="synthetic-owner"
    assert result.directory_revision=="fixture-in-memory-1"
    assert result.production_execution_authorized is False
    assert result.release_authorized is False


@pytest.mark.parametrize("extra", [
    {"tenant_id":"synthetic-tenant-a"},
    {"tenant_id":"synthetic-tenant-b"},
    {"tenant_id":None},
    {"principal":"synthetic-unknown"},
    {"cypher":"MATCH (n) RETURN n"},
    {"limit":999},
    {"filters": {"tenant_id":"synthetic-tenant-b"}},
    {"status":"READY","tenant":"synthetic-tenant-b"},
])
def test_matching_or_alternate_caller_scope_is_always_refused(extra):
    data=inputs()
    data["request"]={**data["request"],**extra}
    with pytest.raises(BrokerBoundaryDenied, match="UNREVIEWED_CLIENT_SCOPE"):
        resolve_scoped_read(**data)


@pytest.mark.parametrize("candidate", [
    None, {}, [], {"plan_id":"bad-plan", "status":"READY"},
    {"plan_id":TENANT_PLAN_ID, "status":"unknown"},
    {"plan_id":TENANT_PLAN_ID, "status":True},
])
def test_unknown_plan_status_and_request_shape_denied(candidate):
    with pytest.raises(BrokerBoundaryDenied):
        resolved(request=candidate)


@pytest.mark.parametrize("changes", [
    {"credentials":None},
    {"credentials":HTTPBasicCredentials(username="synthetic-owner",password="incorrect")},
    {"credentials":HTTPBasicCredentials(username="another",password="fixture-secret")},
    {"principal":"synthetic-unknown"},
    {"configured_password":None},
    {"allowed_basic_operators":""},
    {"allowed_basic_operators":"another"},
    {"server_owned_directory":None},
    {"server_owned_directory":ServerOwnedDirectory(
        revision="fixture-2",principal_tenants={"another":"synthetic-tenant-a"})},
    {"server_owned_directory":ServerOwnedDirectory(
        revision="fixture-2",principal_tenants={"synthetic-owner":"NO/INVALID/TENANT"})},
])
def test_no_header_only_identity_bad_basic_unknown_operator_or_missing_mapping(changes):
    with pytest.raises(BrokerBoundaryDenied):
        resolved(**changes)


def test_mutating_fixture_mapping_after_copy_is_not_a_production_provenance():
    d=inputs()["server_owned_directory"]
    assert not d.production_issuer_verified
    assert not d.signed_by_independent_authority
    fake=replace(d,signed_by_independent_authority=True)
    with pytest.raises(BrokerBoundaryDenied,match="FAKE_FIXTURE_CANNOT_CLAIM_PRODUCTION"):
        resolved(server_owned_directory=fake)


def test_single_configured_basic_identity_does_not_allow_second_principal():
    second=inputs()
    second["principal"]="synthetic-unknown"
    second["credentials"]=HTTPBasicCredentials(
        username="synthetic-unknown", password="fixture-secret")
    second["allowed_basic_operators"]="synthetic-owner,synthetic-unknown"
    with pytest.raises(BrokerBoundaryDenied,match="VERIFIED_BASIC_OPERATOR_REQUIRED"):
        resolve_scoped_read(**second)


def test_current_task_writers_have_no_tenant_property_contract():
    # Source binding only; not a claim that live graph contains no legacy tenants.
    source=Path(__file__).resolve().parents[1]/"src/assistx/neo4j_client.py"
    text=source.read_text()
    start=text.index("    def upsert_ticket(")
    stop=text.index("    def create_tasks_batch(",start)
    upsert=text[start:stop]
    start_batch=stop
    end_batch=text.index("    def ",start_batch+8)
    batch=text[start_batch:end_batch]
    for code in (upsert,batch):
        assert '"tenant_id"' not in code
        assert "created_at_ts" in code
        assert "payload_json" in code


def test_current_harness_route_has_no_per_caller_task_scope():
    code=(Path(__file__).resolve().parents[1]/"src/assistx/api.py").read_text()
    begin=code.index('def api_harness_evolution(')
    end=code.index('\n\n@app.',begin)
    route=code[begin:end]
    assert "Depends(auth)" in route
    assert "neo.get_tasks_by_status(status, limit=40)" in route
    assert "tenant_id" not in route


def test_no_real_broker_is_connected_and_holds_are_explicit():
    d=release_decision()
    assert all(value is False for key,value in d.items() if key != "schema")
    code=(Path(__file__).with_name("trace_b02_tenant_broker_boundary.py")).read_text()
    for token in ("GraphDatabase.driver(", "subprocess", "docker ", "app.get(",
                  "requests.get(", "Neo4jClient(", "close_with_witness("):
        assert token not in code
