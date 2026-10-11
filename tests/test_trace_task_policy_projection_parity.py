"""Exact-source policy parity for an explicit restricted claim-guard read.

Never substitute the two-field Task read plan into the security-sensitive
claim/recovery endpoints. This tests *only* current recovery/drain decisions,
not full Task semantics, other admission rules or production routing.
"""
import json

import pytest

from test_trace_task_policy_projection_negative import source_function
from test_trace_isolated_task_broker import ok_document
from trace_disposable_task_broker import (
    CLAIM_PLAN, CLAIM_CYPHER, CYPHER, PLAN, RequestDenied,
    decode_task_response, validate,
)


def projection(**changes):
    value = {
        "id": "fixture-task-abcd", "status": "READY",
        "payload_json": None, "queue_class": None,
        "required_capabilities": [],
    }
    value.update(changes)
    return decode_task_response(ok_document(value), CLAIM_PLAN)


def source_claim_policy():
    def _json_dict(raw):
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw) if isinstance(raw, str) else {}
        except (TypeError, ValueError):
            return {}
    return source_function("_is_claim_allowed_for_workflow_control", {
        "Dict": dict, "Any": object,
        "_get_workflow_control": lambda: {"mode": "drain"},
        "_json_dict": _json_dict,
    })


def test_distinct_reviewed_cypher_plans_share_only_task_id_parameter():
    assert PLAN == "get_task_read"
    assert CLAIM_PLAN == "get_task_claim_guard"
    assert CYPHER == "MATCH (t:Task {id: $id}) RETURN t { .id, .status } AS task"
    assert ".required_capabilities" in CLAIM_CYPHER
    assert ".payload_json" in CLAIM_CYPHER
    assert ".queue_class" in CLAIM_CYPHER
    assert "CREATE" not in CLAIM_CYPHER and "SET" not in CLAIM_CYPHER
    assert validate({"plan_id": CLAIM_PLAN, "parameters": {
        "id": "fixture-task-abcd"}}) == "fixture-task-abcd"


def test_actual_recovery_helper_preserves_identity_gate_with_policy_projection():
    actual = {
        "id": "fixture-task-abcd", "status": "READY",
        "payload_json": '{"runbook":"fixture-only"}',
        "required_capabilities": ["recovery"],
        "queue_class": None,
    }
    recovered = projection(**actual)
    check = source_function("_is_recovery_task", {"json": json})
    assert check(actual) is True
    assert check(recovered) is True
    assert check({"id": actual["id"], "status": actual["status"]}) is False


def test_actual_drain_mode_preserves_critical_queue_policy():
    actual = {
        "id": "fixture-task-abcd", "status": "READY",
        "payload_json": '{"queue_class":"critical"}',
        "queue_class": "critical", "required_capabilities": [],
    }
    recovered = projection(**actual)
    check = source_claim_policy()
    assert check(actual) == (True, "")
    assert check(recovered) == (True, "")
    assert check({"id": actual["id"], "status": actual["status"]})[0] is False


def test_actual_drain_mode_rejects_interactive_task_after_projection():
    actual = {
        "id": "fixture-task-abcd", "status": "READY",
        "payload_json": '{"queue_class":"interactive"}',
        "queue_class": "interactive", "required_capabilities": [],
    }
    check = source_claim_policy()
    assert check(actual)[0] is False
    assert check(projection(**actual))[0] is False


@pytest.mark.parametrize("truncated", [
    {"id": "fixture-task-abcd", "status": "READY"},
    {"id": "fixture-task-abcd", "status": "READY",
     "required_capabilities": ["recovery"],
     "payload_json": '{"runbook":"fixture-only"}'},
    {"id": "fixture-task-abcd", "status": "READY",
     "queue_class": "critical", "payload_json": '{"queue_class":"critical"}'},
])
def test_missing_any_policy_field_does_not_downgrade_to_ordinary_task(truncated):
    with pytest.raises(RequestDenied, match="POLICY_FIELDS_MISSING"):
        decode_task_response(ok_document(truncated), CLAIM_PLAN)


@pytest.mark.parametrize("bad", [
    {"required_capabilities": "recovery"},
    {"required_capabilities": ["recovery", 99]},
    {"required_capabilities": ["recovery"] * 40},
    {"queue_class": {"critical": True}},
    {"payload_json": {"queue_class": "critical"}},
])
def test_policy_field_types_fail_closed(bad):
    with pytest.raises(RequestDenied, match="UNAPPROVED_FIELDS"):
        projection(**bad)


def test_minimal_plan_does_not_leak_extended_claim_policy_fields():
    with pytest.raises(RequestDenied, match="UNAPPROVED_FIELDS"):
        decode_task_response(ok_document({
            "id": "fixture-task-abcd", "status": "READY",
            "required_capabilities": ["recovery"],
        }), PLAN)
