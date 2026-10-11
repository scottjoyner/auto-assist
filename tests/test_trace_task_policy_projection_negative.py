"""Policy-semantic counterexamples to unsafe two-field Task broker migration.

The disposable Task broker currently returns only id/status. Actual AssistX
task-claim/recovery functions need required_capabilities, payload_json and
queue_class. These tests extract the *exact source functions* (not mocks) with
only their runtime dependencies supplied, proving the projection cannot be
silently used in those security decisions.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path


API_SOURCE = (Path(__file__).resolve().parents[1] /
              "src/assistx/api.py")
REQUIRED_POLICY_FIELDS = frozenset({
    "required_capabilities", "payload", "payload_json", "queue_class",
})


def source_function(name: str, scope: dict):
    tree = ast.parse(API_SOURCE.read_text())
    fn = [node for node in tree.body if (
        isinstance(node, ast.FunctionDef) and node.name == name)]
    if len(fn) != 1:
        raise AssertionError(f"EXPECTED_EXACTLY_ONE_SOURCE_FUNCTION:{name}")
    compile_tree = ast.Module(body=fn, type_ignores=[])
    exec(compile(compile_tree, str(API_SOURCE), "exec"), scope)
    return scope[name]


def test_recovery_task_loses_auth_requiring_capability_in_two_field_projection():
    check_recovery = source_function("_is_recovery_task", {"json": json})
    actual = {
        "id": "fixture-task-recov",
        "status": "READY",
        "required_capabilities": ["recovery"],
        "payload_json": json.dumps({"runbook": "fixture-only"}),
    }
    projected = {"id": actual["id"], "status": actual["status"]}
    assert check_recovery(actual) is True
    assert check_recovery(projected) is False
    assert not REQUIRED_POLICY_FIELDS.issubset(projected)
    # This is an explicit NO-GO counterexample: if the live API were fed
    # only the projection, it could skip _verify_recovery_node_identity.


def test_drain_mode_misclassifies_critical_task_when_payload_is_missing():
    def parse_payload(raw):
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw) if isinstance(raw, str) else {}
        except (TypeError, ValueError):
            return {}

    check = source_function("_is_claim_allowed_for_workflow_control", {
        "Dict": dict, "Any": object,
        "_get_workflow_control": lambda: {"mode": "drain"},
        "_json_dict": parse_payload,
    })
    actual = {
        "id": "fixture-task-crit",
        "status": "READY",
        "payload_json": json.dumps({"queue_class": "critical"}),
    }
    projected = {"id": actual["id"], "status": actual["status"]}
    assert check(actual) == (True, "")
    allowed, reason = check(projected)
    assert not allowed
    assert "interactive" in reason


def test_broker_returns_only_two_fields_so_security_parity_not_claimed():
    broker = Path(__file__).with_name("trace_disposable_task_broker.py")
    source = broker.read_text()
    assert 'allowed = {"id", "status"}' in source
    assert 'MATCH (t:Task {id: $id}) RETURN t { .id, .status } AS task' in source
    assert REQUIRED_POLICY_FIELDS - {"id", "status"}
