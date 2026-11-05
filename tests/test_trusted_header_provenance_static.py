"""No-socket, stdlib-only P0 #149 trusted-header provenance regression.

The production Basic auth dependency must never treat a request-supplied
identity header as credential proof without independently verified ingress
custody. This test does not import the app, read credentials, or use network.
"""
from __future__ import annotations

import ast
from pathlib import Path


API = Path(__file__).resolve().parents[1] / "src" / "assistx" / "api.py"


def _nodes():
    tree = ast.parse(API.read_text(encoding="utf-8"))
    return {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_no_header_only_principal_accepted_in_shared_auth():
    funcs = _nodes()
    fn = funcs["_auth_user_from_credentials"]
    text = ast.unparse(fn)
    assert "TRUSTED_AUTH_HEADER" not in text
    assert "request.headers" not in text
    assert "compare_digest(credentials.username, USER)" in text
    assert "compare_digest(credentials.password, PASS)" in text
    assert "username_ok and password_ok" in text


def test_operator_and_machine_auth_route_through_common_function():
    funcs = _nodes()
    assert "_auth_user_from_credentials(request, credentials)" in ast.unparse(funcs["auth"])
    assert "_auth_user_from_credentials(request, credentials)" in ast.unparse(
        funcs["fleet_node_or_basic_auth"]
    )
    assert "evaluate_fleet_node_identity" in ast.unparse(funcs["fleet_node_or_basic_auth"])


def test_trace_page_and_task_listing_use_protected_auth_dependencies():
    funcs = _nodes()
    trace = ast.unparse(funcs["traces_ui"])
    task = ast.unparse(funcs["api_agent_tasks"])
    assert "Depends(auth)" in trace
    assert "Depends(fleet_node_or_basic_auth)" in task
    assert "_enforce_fleet_node_binding" in task
