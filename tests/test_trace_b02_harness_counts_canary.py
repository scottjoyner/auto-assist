"""B02 counts-only contract. No task content or runtime query execution."""
from __future__ import annotations
from pathlib import Path
import json
import pytest
from trace_b02_harness_counts_canary import (
    CountsDenied, fixed_params, validate_rows, QUERY, TASK_KINDS, STATUSES,
    MAX_ROWS, PLAN_ID,
)
import probe_trace_b02_harness_counts_readonly as live

def test_source_pinned_harness_kinds_and_statuses():
    from assistx.harness_views import HARNESS_TASK_KINDS, LIVE_STATUSES
    assert tuple(HARNESS_TASK_KINDS) == TASK_KINDS
    assert tuple(LIVE_STATUSES) == STATUSES
    assert live.KINDS == TASK_KINDS
    assert live.STATUSES == STATUSES
    assert QUERY == live.QUERY
    assert live.PLAN_ID == PLAN_ID
    assert len(TASK_KINDS) == 5 and len(STATUSES) == 6
    assert MAX_ROWS == 30

def test_fixed_query_emits_only_kind_status_count():
    assert "RETURN t.kind AS kind, t.status AS status, count(t) AS n" in QUERY
    assert "IN $kinds" in QUERY and "IN $statuses" in QUERY
    assert "LIMIT $" not in QUERY
    for banned in ("t.payload_json","t.title","t.id","t.last_error",
                   "t.summary","RETURN t AS", "SET t.", "MERGE ", "DELETE "):
        assert banned not in QUERY
    assert fixed_params() == {"kinds":list(TASK_KINDS),"statuses":list(STATUSES)}

def test_aggregate_validates_sparse_rows_without_data_values():
    data=validate_rows([
        {"kind":"harness_reflect","status":"READY","n":18},
        {"kind":"harness_deploy","status":"ERROR","n":2}])
    assert data["matching_task_count"]==20
    assert data["kind_counts"]["harness_reflect"]==18
    assert data["status_counts"]["ERROR"]==2
    assert data["kind_counts"]["harness_train"]==0
    assert data["contains_task_values"] is False
    assert data["real_data_global_operator_access_authorized"] is False
    assert data["production_http_route_enabled"] is False

@pytest.mark.parametrize("rows",[
    "anything", None, [], # placeholder special below
])
def test_malformed_container_fails_closed(rows):
    if rows == []:
        assert validate_rows(rows)["matching_task_count"] == 0
    else:
        with pytest.raises(CountsDenied):
            validate_rows(rows)

@pytest.mark.parametrize("invalid", [
    [{"kind":"harness_reflect","status":"READY","n":True}],
    [{"kind":"harness_reflect","status":"READY","n":-1}],
    [{"kind":"harness_reflect","status":"READY","n":"10"}],
    [{"kind":"harness_reflect","status":"READY","n":100000001}],
    [{"kind":"unknown-kind","status":"READY","n":1}],
    [{"kind":"harness_reflect","status":"unknown","n":1}],
    [{"kind":"harness_reflect","status":"READY","n":1,"task_id":"secret"}],
    [{"kind":"harness_reflect","status":"READY"}],
    [{"kind":"harness_reflect","status":"READY","n":1},
     {"kind":"harness_reflect","status":"READY","n":2}],
    [{"kind":"harness_reflect","status":"READY","n":0}]*31,
])
def test_untrusted_or_raw_rows_refused(invalid):
    with pytest.raises(CountsDenied):
        validate_rows(invalid)

def test_offline_live_probe_denies_without_opt_in(monkeypatch):
    monkeypatch.delenv("ASSISTX_B02_COUNTS_READONLY",raising=False)
    with pytest.raises(ValueError, match="EXPLICIT_OPERATOR_OPT_IN"):
        live.run()

def test_no_config_means_no_graph_driver_access(monkeypatch):
    monkeypatch.setenv("ASSISTX_B02_COUNTS_READONLY","1")
    for name in ("NEO4J_URI","NEO4J_USER","NEO4J_PASSWORD","NEO4J_DATABASE"):
        monkeypatch.delenv(name,raising=False)
    with pytest.raises(ValueError, match="EXISTING_NEO4J_CONFIG_REQUIRED"):
        live.run()

def test_scripts_never_enable_user_endpoint_or_write_data():
    source=Path(__file__).with_name("trace_b02_harness_counts_canary.py").read_text()
    probe=Path(__file__).with_name("probe_trace_b02_harness_counts_readonly.py").read_text()
    assert "default_access_mode=READ_ACCESS" in probe
    assert "max_connection_pool_size=1" in probe
    assert "timeout=3" in probe
    for s in (source,probe):
        for forbidden in ("app.get(", "GraphDatabase.driver(" if s==source else "app.post(",
                          "subprocess.run(", "CREATE (", "session.execute_write",
                          "TRACE_PREVIEW_BASIC_USERS"):
            assert forbidden not in s

def test_aggregate_report_never_contains_row_data():
    result=json.dumps(validate_rows([{"kind":"harness_train","status":"RUNNING","n":1}]))
    for term in ("payload_json","task_id","password","secret", "title", "endpoint"):
        assert term not in result
