"""B02 off-production harness Task projection field- and behavior-guards."""
from __future__ import annotations
import importlib.util
from pathlib import Path
from copy import deepcopy
import pytest

from trace_b02_harness_projection_candidate import (
    PLAN_ID, CYPHER, KEYS, ProjectionDenied, params,
    project_legacy_task, validate_broker_rows,
    run_shadow_parity, research_disposition,
)

P = Path(__file__).resolve().parents[1] / "src/assistx/harness_views.py"
spec = importlib.util.spec_from_file_location("b02_pinned_harness_views",P)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
snapshot = module.harness_evolution_snapshot

def fixture():
    tasks=[
        dict(id="task-h",kind="harness_reflect",title="Research outcome",
             status="FAILED",target_agent_id="worker-1",
             payload_json='{"chain":{"chain_id":"chain-001"},"suite_id":"suite-1","fail_set":[{"task_id":"x","expected":"ok","actual":"wrong"}]}',
             updated_at_ts=100,summary="not yet",last_error="error seen",
             internal_secret="NEVER_RETURNS",extra_field="ignored"),
        dict(id="task-2",kind="harness_deploy",title="Apply artifact",
             status="READY",target_agent_id=None,
             payload_json='{"endpoint":"local","suite_id":"suite-1"}',
             updated_at_ts=200,summary=None,last_error=None,
             unreviewed_property="NEVER_RETURNS"),
        dict(id="unrelated",kind="other_task",title="misc",
             status="DONE",target_agent_id=None,payload_json=None,
             updated_at_ts=1,summary=None,last_error=None),
    ]
    runs=[dict(id="run-1",status="complete",created_at_ts=70,
               metadata_json='{"harness_evolution":true,"chain_id":"chain-001","stage":"reflect","suite_id":"suite-1"}')]
    return runs,tasks

def test_shadow_output_equal_on_representative_full_row():
    runs,tasks=fixture()
    assert run_shadow_parity(runs=runs,raw_tasks=tasks,snapshot_fn=snapshot)

def test_projection_has_nine_explicit_fields_and_no_secret_echo():
    _,tasks=fixture()
    projected=project_legacy_task(tasks[0])
    assert set(projected)==KEYS
    assert "internal_secret" not in projected
    assert "extra_field" not in projected

def test_existing_harness_output_preserves_fail_set_and_title():
    runs,tasks=fixture()
    legacy=snapshot(runs,tasks)
    projected=snapshot(runs,[project_legacy_task(x) for x in tasks])
    assert legacy==projected
    assert legacy["live_tasks"][0]["objective"]=="Apply artifact"
    assert any(x["source_task_id"]=="task-h" for x in legacy["mistakes"])
    assert legacy["chains"][0]["chain_id"]=="chain-001"

def test_no_dynamic_cypher_or_operator_parameters():
    assert params("READY") == {"status":"READY","limit":40}
    assert PLAN_ID=="harness_evolution_tasks_read_v1"
    assert "RETURN t {" in CYPHER
    assert "ORDER BY t.created_at_ts DESC LIMIT $limit" in CYPHER
    for banned in ("SET ","MERGE ","DELETE ","CREATE ","CALL ","DROP "):
        assert banned not in CYPHER

@pytest.mark.parametrize("status,limit", [
    ("UNKNOWN",40),("ready",40),("READY",41),("READY",True),
    (None,40),("READY","40"),
])
def test_out_of_scope_status_or_limit_denied(status,limit):
    with pytest.raises(ProjectionDenied):
        params(status,limit)

def test_missing_status_or_title_refuses_silent_downgrade():
    _,tasks=fixture()
    for key in ("title","payload_json","status","updated_at_ts"):
        broken=deepcopy(tasks[0]);del broken[key]
        with pytest.raises(ProjectionDenied):
            project_legacy_task(broken)

def test_extra_broker_projection_field_rejected():
    _,tasks=fixture()
    row=project_legacy_task(tasks[0]);row["raw_token"]="x"
    with pytest.raises(ProjectionDenied,match="MISSING_OR_EXTRA"):
        validate_broker_rows([row])

def test_wrong_types_or_excess_payload_refused():
    _,tasks=fixture()
    row=project_legacy_task(tasks[0]);row["updated_at_ts"]=True
    with pytest.raises(ProjectionDenied):validate_broker_rows([row])
    row=project_legacy_task(tasks[0]);row["payload_json"]="x"*65537
    with pytest.raises(ProjectionDenied):validate_broker_rows([row])

def test_maximum_rows_fail_closed():
    _,tasks=fixture()
    row=project_legacy_task(tasks[0])
    with pytest.raises(ProjectionDenied):validate_broker_rows([row]*41)

def test_candidate_never_approves_real_graph_migration():
    result=research_disposition()
    assert result["physical_broker_accepted"] is False
    assert result["production_graph_cutover_authorized"] is False
    assert result["release_authorized"] is False
    source=Path(__file__).with_name("trace_b02_harness_projection_candidate.py").read_text()
    for bad in ("import neo4j","GraphDatabase.driver","import requests","subprocess","docker ","os.environ"):
        assert bad not in source
