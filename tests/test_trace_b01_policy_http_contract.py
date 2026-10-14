"""Offline fail-closed contract for disposable HTTP Task policy plan."""
from pathlib import Path
import pytest

from trace_disposable_task_broker import (
    CLAIM_CYPHER, POLICY_PLAN, POLICY_SOURCE, STATIC_PLANS,
    RequestDenied, decode_task_response, validate,
    verify_fixture_identity,
)

def good_document(row):
    return {"results":[{"columns":["task"],"data":[{"row":[row]}]}],
            "errors":[]}

def policy_row():
    return dict(id="fixture-task-recov", status="READY",
                payload=None,payload_json='{"runbook":"synthetic"}',
                queue_class=None,required_capabilities=["recovery"])

def payload(**changes):
    p={"plan_id":POLICY_PLAN,"parameters":{
        "id":"fixture-task-recov","agent_id":"fixture-agent-test123"}}
    p["parameters"].update(changes)
    return p

def test_policy_plan_reuses_fixed_six_field_cypher():
    assert STATIC_PLANS[POLICY_PLAN] == CLAIM_CYPHER
    assert "CREATE" not in CLAIM_CYPHER
    assert "SET " not in CLAIM_CYPHER
    assert "DELETE" not in CLAIM_CYPHER
    assert "RETURN t {" in CLAIM_CYPHER
    assert POLICY_SOURCE == Path("/tmp/assistx-policy-api-source.py")
def test_policy_request_accepts_only_fixed_agent_id_and_task():
    assert validate(payload()) == "fixture-task-recov"
    verify_fixture_identity(
        payload()["parameters"], "fixture-good-node-token",
        "fixture-agent-test123", "fixture-good-node-token")

@pytest.mark.parametrize("change",[
    {"agent_id":"fixture-agent-foreign"},
    {"agent_id":None},
    {"agent_id":"fixture-agent-' OR true"},
    {"agent_id":"../x"},
    {"agent_id":""},
    {"query":"MATCH (n) RETURN n"},
    {"tenant_id":"tenant-b"},
    {"cypher":"MATCH (n) RETURN n"},
])
def test_bad_extra_agent_or_query_scope_denied(change):
    with pytest.raises(RequestDenied):
        validate(payload(**change)) if change.get("agent_id") != "fixture-agent-foreign" else verify_fixture_identity(
            payload(**change)["parameters"],"fixture-good-node-token",
            "fixture-agent-test123","fixture-good-node-token")

@pytest.mark.parametrize("token,agent",[
    ("", "fixture-agent-test123"),
    ("wrong-token","fixture-agent-test123"),
    ("fixture-good-node-token","fixture-agent-foreign"),
    ("fixture-good-node-token",""),
])
def test_fixture_identity_requires_valid_agent_and_node_token(token,agent):
    with pytest.raises(RequestDenied,match="NODE_IDENTITY_REQUIRED"):
        verify_fixture_identity(
            {"agent_id":agent},token,
            "fixture-agent-test123","fixture-good-node-token")
def test_policy_decoder_accepts_only_six_fields():
    result=decode_task_response(good_document(policy_row()), POLICY_PLAN)
    assert set(result)=={
        "id","status","payload","payload_json",
        "queue_class","required_capabilities"}

@pytest.mark.parametrize("changed",[
    {"extra":"PRIVATE"},
    {"required_capabilities":"recovery"},
    {"status":42},
    {"payload_json":42},
    {"queue_class":False},
])
def test_corrupted_graph_result_denies(changed):
    with pytest.raises(RequestDenied):
        decode_task_response(
            good_document(dict(policy_row(),**changed)), POLICY_PLAN)

def test_only_synthetic_fixture_sources_are_loaded_in_container():
    source=Path(__file__).with_name(
        "probe_trace_isolated_task_broker.py").read_text()
    assert "trace_b01_task_policy_classification.py" in source
    assert "trace_b01_task_consumer_parity.py" in source
    assert "assistx-policy-api-source.py" in source
    assert '"policy_agent_token": policy_agent_token' in source
    assert "ASSISTX_DISPOSABLE_GRAPH_BROKER" in source

def test_http_handler_enforces_bearer_and_node_before_graph():
    src=Path(__file__).with_name("trace_disposable_task_broker.py").read_text()
    pos_bearer=src.index('if not compare_digest(actual, "Bearer "')
    pos_node=src.index("verify_fixture_identity(",src.index("def do_POST"))
    pos_fetch=src.index("task = fetch_task(",src.index("def do_POST"))
    assert pos_bearer < pos_node < pos_fetch
    assert 'self.reply(403, {"error": "NODE_IDENTITY_REQUIRED"})' in src
    assert 'self.reply(200, {"policy": policy} if is_policy' in src
    assert 'claim_authorized' not in src