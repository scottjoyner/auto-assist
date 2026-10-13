"""Synthetic source-pinned B01 unsafe get_task substitution regression."""
from pathlib import Path
import pytest

from trace_b01_task_consumer_parity import (
    BROKER_BASIC_FIELDS, BROKER_CLAIM_FIELDS, ParityDenied,
    VERDICT, compile_source_helpers, policy_counterexample, source_pins,
)
ROOT = Path(__file__).resolve().parents[1]

def test_legacy_task_and_broker_source_pins():
    result = source_pins(ROOT)
    assert all(result.values())
    assert "payload_json" not in BROKER_BASIC_FIELDS
    assert {"payload", "payload_json", "queue_class",
            "required_capabilities"}.issubset(BROKER_CLAIM_FIELDS)
    assert BROKER_BASIC_FIELDS == {"id", "status"}

def test_exact_api_policies_show_two_field_swap_changes_decisions():
    result = policy_counterexample(ROOT)
    assert result["verdict"] == VERDICT
    assert result["source_pinned"] is True
    assert result["full_recovery_identified"] is True
    assert result["basic_recovery_identified"] is False
    assert result["claim_plan_recovery_identified"] is True
    assert result["full_critical_allowed_in_drain"] is True
    assert result["basic_critical_allowed_in_drain"] is False
    assert result["claim_plan_critical_allowed_in_drain"] is True
    assert result["real_task_parity_proven"] is False
    assert result["production_cutover_authorized"] is False
def test_real_helper_resume_logic_is_still_distinct_from_auth():
    helpers = compile_source_helpers(ROOT/"src/assistx/api.py",mode="resume")
    ordinary={"id":"fixture-task-A1bc","status":"READY"}
    assert helpers._is_recovery_task(ordinary) is False
    assert helpers._is_claim_allowed_for_workflow_control(ordinary)==(True,"")

@pytest.mark.parametrize("invalid", ["pause", "production", "", None, True])
def test_unreviewed_mode_fails_closed(invalid):
    with pytest.raises(ParityDenied):
        compile_source_helpers(ROOT/"src/assistx/api.py",mode=invalid)

def test_missing_api_helpers_fail_closed(tmp_path):
    fake=tmp_path/"api.py"
    fake.write_text("def _is_recovery_task(task):\n    return False\n")
    with pytest.raises(ParityDenied,match="API_CONSUMER_SOURCE_CHANGED"):
        compile_source_helpers(fake,mode="drain")

def test_missing_neo4j_get_task_fails_closed(tmp_path):
    src=tmp_path/"src/assistx"
    src.mkdir(parents=True)
    (src/"neo4j_client.py").write_text("class Neo4jClient:\n    pass\n")
    with pytest.raises(ParityDenied,match="NEO4J_GET_TASK_CHANGED"):
        source_pins(tmp_path)

def test_no_real_api_import_database_or_network_in_source_witness():
    code=Path(__file__).with_name("trace_b01_task_consumer_parity.py").read_text()
    forbidden=("GraphDatabase.driver(", "requests.get(", "urllib.request.urlopen",
               "subprocess.", "docker exec", "Neo4jClient(", "import assistx.api",
               "os.environ", "os.getenv(")
    for marker in forbidden:
        assert marker not in code
    assert "ast.Module(" in code
    assert "return 2" in code