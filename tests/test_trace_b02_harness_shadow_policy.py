"""B02 shadow Basic-auth, privacy-minimization and remaining HOLD contracts."""
import importlib.util
import json
from pathlib import Path
from fastapi.security import HTTPBasicCredentials
import pytest

from trace_b02_harness_shadow_policy import (
    ShadowDenied, ShadowPolicy, authorize_shadow, disposition,
    minimize_payload, minimize_task,
)
from trace_b02_harness_projection_candidate import KEYS

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location(
    "b02_exact_harness_snapshot",ROOT/"src/assistx/harness_views.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def auth(**changes):
    kwargs=dict(principal="fixture-operator",
                credentials=HTTPBasicCredentials(username="fixture-operator",
                                                 password="fixture-password"),
                configured_user="fixture-operator",
                configured_password="fixture-password",
                policy=ShadowPolicy(operator_users="fixture-operator",
                                    reviewed_global_operator_scope=True,
                                    explicit_shadow_mode=True))
    kwargs.update(changes)
    return authorize_shadow(**kwargs)

def task():
    return dict(id="fixture-1",kind="harness_reflect",title="Fixture",
                status="FAILED",target_agent_id="fixture-worker",
                payload_json=json.dumps({"chain":{"chain_id":"fixture-chain","token":"SYNTHETIC-SECRET"},
                    "suite_id":"suite-a","endpoint":"model-local",
                    "fail_set":[{"task_id":"t1","expected":"yes","actual":"no",
                                 "auth_secret":"SYNTHETIC-SECRET"}],
                    "api_key":"SYNTHETIC-SECRET","password":"SYNTHETIC-SECRET"}),
                updated_at_ts=100,summary="done",last_error="some error")

def test_verified_basic_and_explicit_operator_scope_still_not_production_approval():
    result=auth()
    assert result.principal=="fixture-operator"
    assert result.real_data_access_authorized is False
    assert result.tenant_isolation_proven is False
    assert result.production_route_authorized is False
    assert result.release_authorized is False

@pytest.mark.parametrize("change", [
    {"credentials":None},
    {"credentials":HTTPBasicCredentials(username="fixture-operator",password="bad")},
    {"credentials":HTTPBasicCredentials(username="another",password="fixture-password")},
    {"configured_password":None},
    {"configured_user":None},
    {"principal":"header-forged-fixture"},
    {"policy":ShadowPolicy(operator_users="",reviewed_global_operator_scope=True,explicit_shadow_mode=True)},
    {"policy":ShadowPolicy(operator_users="fixture-operator",reviewed_global_operator_scope=False,explicit_shadow_mode=True)},
    {"policy":ShadowPolicy(operator_users="fixture-operator",reviewed_global_operator_scope=True,explicit_shadow_mode=False)},
])
def test_header_only_or_unreviewed_scope_never_authorizes_shadow(change):
    with pytest.raises(ShadowDenied):
        auth(**change)

def test_extra_payload_secrets_are_removed_without_changing_harness_view_output():
    row=task()
    reduced=minimize_task(row)
    assert set(reduced)==KEYS
    assert "SYNTHETIC-SECRET" not in reduced["payload_json"]
    assert "api_key" not in reduced["payload_json"]
    assert "auth_secret" not in reduced["payload_json"]
    before=module.harness_evolution_snapshot([], [row])
    after=module.harness_evolution_snapshot([], [reduced])
    assert before==after
    assert after["mistakes"][0]["task_id"]=="t1"

def test_missing_optional_payload_is_preserved():
    row=task();row["payload_json"]=None
    assert minimize_task(row)["payload_json"] is None

@pytest.mark.parametrize("payload",[
    '{"endpoint":"https://name:pass@host/path"}',
    '{"endpoint":"https://host/path?api_key=x"}',
    '{"endpoint":"https://host/path#secret"}',
    '{"endpoint":42}',
    '{"chain":[42]}',
    '{"fail_set":"wrong"}',
    '{"fail_set":[1]}',
    '{"fail_set":'+json.dumps([{"task_id":"x"}]*33)+'}',
    "{not valid",
    "x"*65537,
    "[]",
])
def test_unreviewed_risky_or_malformed_payload_fails_closed(payload):
    with pytest.raises(ShadowDenied):
        minimize_payload(payload)

def test_selected_fields_must_still_be_exact():
    row=task(); row["unexpected"]="secret"
    with pytest.raises(ShadowDenied):
        minimize_task(row)

def test_privacy_claim_remains_unproven_despite_minimization():
    status=disposition()
    assert status["trusted_header_only_denied"] is True
    assert status["payload_value_pii_review_complete"] is False
    assert status["tenant_isolation_proven"] is False
    assert status["global_operator_scope_accepted"] is False
    assert status["staging_shadow_enabled"] is False

def test_module_contains_no_live_route_or_runtime_graph_connector():
    code=Path(__file__).with_name("trace_b02_harness_shadow_policy.py").read_text()
    for bad in ("GraphDatabase.driver","import neo4j", "docker", "subprocess",
                "app.get(", "os.getenv", "urlopen(", "close_with_witness"):
        assert bad not in code


def test_failure_text_can_still_contain_sensitive_content_and_needs_policy_review():
    row=task()
    row["payload_json"]=json.dumps({
        "fail_set":[{"task_id":"fixture-x",
                     "actual":"SYNTHETIC-SENSITIVE-ERROR"}]})
    reduced=minimize_task(row)
    assert "SYNTHETIC-SENSITIVE-ERROR" in reduced["payload_json"]
    assert disposition()["payload_value_pii_review_complete"] is False

def test_unscoped_source_query_lacks_tenant_authorization():
    from trace_b02_harness_projection_candidate import CYPHER
    assert "tenant_id" not in CYPHER
    assert "user_id" not in CYPHER
    assert "principal" not in CYPHER
    assert disposition()["tenant_isolation_proven"] is False
