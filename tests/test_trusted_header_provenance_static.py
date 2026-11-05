"""No-socket, dependency-free P0 #149 trusted-header provenance regression.

A caller-supplied identity header may never substitute for independently
verified operator Basic credentials. This script does not import AssistX,
read environment secrets or access the network.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


API = Path(__file__).resolve().parents[1] / "src" / "assistx" / "api.py"


def _functions() -> dict:
    tree = ast.parse(API.read_text(encoding="utf-8"))
    return {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class TrustedHeaderProvenanceSource(unittest.TestCase):
    def test_no_header_only_principal_accepted_in_shared_auth(self):
        fn = _functions()["_auth_user_from_credentials"]
        code = ast.unparse(fn)
        self.assertNotIn("TRUSTED_AUTH_HEADER", code)
        self.assertNotIn("request.headers", code)
        self.assertIn("compare_digest(credentials.username, USER)", code)
        self.assertIn("compare_digest(credentials.password, PASS)", code)
        self.assertIn("username_ok and password_ok", code)

    def test_operator_and_machine_auth_route_through_common_function(self):
        fn = _functions()
        self.assertIn(
            "_auth_user_from_credentials(request, credentials)",
            ast.unparse(fn["auth"]),
        )
        machine = ast.unparse(fn["fleet_node_or_basic_auth"])
        self.assertIn("_auth_user_from_credentials(request, credentials)", machine)
        self.assertIn("evaluate_fleet_node_identity", machine)

    def test_trace_and_task_routes_remain_guarded(self):
        fn = _functions()
        self.assertIn("Depends(auth)", ast.unparse(fn["traces_ui"]))
        task = ast.unparse(fn["api_agent_tasks"])
        self.assertIn("Depends(fleet_node_or_basic_auth)", task)
        self.assertIn("_enforce_fleet_node_binding", task)


if __name__ == "__main__":
    unittest.main()
