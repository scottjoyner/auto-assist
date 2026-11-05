"""Source-only invariant tests: node identity never opens legacy graph Task APIs.

These tests parse code but never import the app, read secrets, connect to DB, or
make network requests. They remain executable in a stdlib-only environment.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

API = Path(__file__).resolve().parents[1] / "src" / "assistx" / "api.py"
ROUTES = (
    "api_agent_tasks", "api_claim_task", "api_heartbeat_task",
    "api_complete_task", "api_checkpoint_task",
)


def _functions():
    tree = ast.parse(API.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


class MachineTaskLegacyFenceTests(unittest.TestCase):
    def test_all_legacy_handlers_depend_on_same_authentication(self):
        fn = _functions()
        for name in ROUTES:
            with self.subTest(name=name):
                route = fn[name]
                dependencies = [
                    n for n in ast.walk(route)
                    if isinstance(n, ast.Call) and _call_name(n) == "Depends"
                ]
                self.assertTrue(
                    any(
                        n.args and isinstance(n.args[0], ast.Name)
                        and n.args[0].id == "fleet_node_or_basic_auth"
                        for n in dependencies
                    ),
                    f"{name}: no explicit node-aware authentication",
                )

    def test_machine_fence_is_unconditional_before_graph_reads_or_writes(self):
        fn = _functions()
        body = fn["_enforce_fleet_node_binding"].body
        # The policy may check identity first. It MUST then reject a verified
        # machine principal in a separate unconditional branch.
        matches = [
            n for n in body if isinstance(n, ast.If)
            and isinstance(n.test, ast.Compare)
            and any(isinstance(op, ast.IsNot) for op in n.test.ops)
            and any(isinstance(t, ast.Constant) and t.value is None for t in n.test.comparators)
            and any(
                isinstance(call, ast.Call) and _call_name(call) == "getattr"
                and any(isinstance(a, ast.Constant) and a.value == "fleet_node_id" for a in call.args)
                for call in ast.walk(n.test)
            )
        ]
        self.assertEqual(len(matches), 1, "missing or ambiguous machine deny")
        deny = matches[0]
        self.assertTrue(
            any(isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
                and _call_name(n.exc) == "HTTPException"
                and any(kw.arg == "status_code" and isinstance(kw.value, ast.Constant)
                        and kw.value.value == 403 for kw in n.exc.keywords)
                for n in ast.walk(deny)
            ),
            "machine identity is not rejected with 403",
        )
        for name in ROUTES:
            with self.subTest(route=name):
                route = fn[name]
                calls = sorted(
                    ((n.lineno, _call_name(n)) for n in ast.walk(route)
                     if isinstance(n, ast.Call)), key=lambda x: x[0]
                )
                guards = [line for line, call in calls if call == "_enforce_fleet_node_binding"]
                graph = [line for line, call in calls if call in {
                    "_neo", "_neo_fleet", "get_task", "claim_task", "list_agent_tasks",
                    "heartbeat_task", "complete_task", "checkpoint_task"
                }]
                self.assertEqual(len(guards), 1, f"{name}: expected exactly one guard")
                self.assertTrue(graph, f"{name}: graph operation inventory changed")
                self.assertLess(guards[0], min(graph), f"{name}: guard runs after graph call")

    def test_machine_identity_probe_is_read_only_and_explicitly_nonadmitting(self):
        fn = _functions()
        probe = fn["api_fleet_node_identity_probe"]
        names = [
            _call_name(n) for n in ast.walk(probe)
            if isinstance(n, ast.Call)
        ]
        forbidden = {
            "_neo", "_neo_fleet", "get_task", "list_agent_tasks",
            "claim_task", "heartbeat_task", "complete_task", "checkpoint_task"
        }
        self.assertFalse(forbidden.intersection(names))
        text = ast.unparse(probe)
        self.assertIn("'machine_identity_verified': True", text)
        self.assertIn("'legacy_task_authorized': False", text)
        self.assertIn("'task_admission': 'HOLD'", text)
        self.assertIn("machine identity required", text)

    def test_node_verification_still_has_no_basic_fallback(self):
        fn = _functions()
        f = fn["fleet_node_or_basic_auth"]
        statements = ast.unparse(f)
        self.assertIn("evaluate_fleet_node_identity(", statements)
        self.assertIn("except FleetNodeAuthHold:", statements)
        self.assertIn("request.state.fleet_node_id = node_id", statements)
        self.assertLess(statements.index("if node_id is not None:"), statements.index("_auth_user_from_credentials("))


if __name__ == "__main__":
    unittest.main()
