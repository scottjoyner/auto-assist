"""Synthetic fixed-policy Task projection: decision parity without raw fields."""
import json
from pathlib import Path
import pytest
from trace_b01_task_policy_classification import (
    ClassificationDenied, SCHEMA, classify_policy,
)
from trace_b01_task_consumer_parity import (
    BROKER_CLAIM_FIELDS, compile_source_helpers,
)

API=Path(__file__).resolve().parents[1]/"src/assistx/api.py"

def task(**updates):
    d=dict(id="fixture-task-A1bc",status="READY",
           payload=None,payload_json=None,
           queue_class=None,required_capabilities=[])
    d.update(updates)
    return d

@pytest.mark.parametrize("case,recovery,drain", [
    (dict(required_capabilities=["recovery"]),True,False),
    (dict(payload_json='{"runbook":"synthetic-only"}'),True,False),
    (dict(queue_class="critical"),False,True),
    (dict(payload_json='{"queue_class":"critical"}'),False,True),
    (dict(payload='{"runbook":"synthetic-only"}',queue_class="critical"),True,True),
    (dict(payload_json='{"runbook":"synthetic-only","queue_class":"critical"}'),True,True),
    (dict(),False,False),
])
def test_strict_projection_matches_source_policy_on_reviewed_shapes(case,recovery,drain):
    full=task(**case)
    result=classify_policy(full,api_source=API)
    assert result["schema"]==SCHEMA
    assert result["requires_recovery_node_identity"] is recovery
    assert result["drain_mode_policy_candidate"] is drain
    assert result["claim_authorized"] is False
    assert result["resource_fence_verified"] is False
    assert result["release_authorized"] is False
    assert set(result)=={"schema","id","status",
                          "requires_recovery_node_identity",
                          "drain_mode_policy_candidate",
                          "claim_authorized","resource_fence_verified",
                          "release_authorized"}
@pytest.mark.parametrize("changes",[
    {"id":"real-production-id-unknown"},
    {"status":"PROPOSED"},
    {"required_capabilities":"recovery"},
    {"required_capabilities":[42]},
    {"required_capabilities":["recovery"]*33},
    {"required_capabilities":["re covery"]},
    {"payload":"not-json"},
    {"payload":"[]"},
    {"payload_json":"{invalid"},
    {"payload_json":"1"},
    {"payload_json":'{"runbook":42}'},
    {"payload_json":'{"runbook":"x","queue_class":"superuser"}'},
    {"queue_class":"unrecognized"},
    {"payload_json":'{"queue_class":"critical"}', "queue_class":"batch"},
    {"payload":'{}',"payload_json":'{"runbook":"synthetic"}'},
    {"payload":'{"runbook":"synthetic"}',"payload_json":'{}'},
    {"payload_json":"x"*16385},
    {"payload_json":'{"runbook":"first","runbook":"second"}'},
    {"payload_json":'{"chain":{"id":1,"id":2}}'},
    {"payload_json":{"unserializable":set([1,2])}},
])
def test_bad_incomplete_conflicting_or_unknown_policy_denied(changes):
    with pytest.raises(ClassificationDenied):
        classify_policy(task(**changes),api_source=API)

def test_absent_and_extra_policy_fields_are_denied():
    a=task()
    for key in BROKER_CLAIM_FIELDS:
        variant=dict(a)
        del variant[key]
        with pytest.raises(ClassificationDenied):
            classify_policy(variant,api_source=API)
    with pytest.raises(ClassificationDenied):
        classify_policy(dict(a,token="PRIVATE-SYNTHETIC"),api_source=API)
def test_drain_policy_recovery_parity_still_source_pinned():
    helpers=compile_source_helpers(API,mode="drain")
    row=task(payload_json='{"runbook":"synthetic","queue_class":"critical"}')
    result=classify_policy(row,api_source=API)
    assert result["requires_recovery_node_identity"] == helpers._is_recovery_task(row)
    assert result["drain_mode_policy_candidate"] == (
        helpers._is_claim_allowed_for_workflow_control(row)[0])

def test_result_never_exposes_runbook_or_capability_values():
    payload='{"runbook":"SYNTHETIC-PRIVATE-KEY","queue_class":"critical"}'
    result=classify_policy(task(payload_json=payload,
                                required_capabilities=["recovery"]),
                           api_source=API)
    output=json.dumps(result)
    assert "SYNTHETIC-PRIVATE-KEY" not in output
    assert "payload" not in output
    assert "required_capabilities" not in output
    assert "queue_class" not in output

def test_pure_module_has_no_runtime_graph_or_api_mount():
    script=Path(__file__).with_name("trace_b01_task_policy_classification.py").read_text()
    for forbidden in ("GraphDatabase.driver", "Neo4jClient(",
                      "app.get(", "app.post(", "subprocess.",
                      "docker", "os.environ", "os.getenv("):
        assert forbidden not in script