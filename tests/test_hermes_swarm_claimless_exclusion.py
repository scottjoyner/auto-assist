"""Hermes cannot be requeued/failed or lease-extended through claimless swarm routes."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from assistx import swarm_core

FILE = Path(swarm_core.__file__)
SCOPE = (
    "coalesce(t.claimed_by, '') STARTS WITH 'hermes-'",
    "coalesce(t.target_agent_id, '') STARTS WITH 'hermes-'",
    "coalesce(t.completed_by, '') STARTS WITH 'hermes-'",
)


def query_for(function: str) -> str:
    tree = ast.parse(FILE.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "run"
        and n.args and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
    ]
    assert len(calls) == 1, function
    return calls[0].args[0].value


def assert_guard_before_write(testcase, query: str, set_clause: str):
    testcase.assertEqual(query.count(set_clause), 1)
    before = query.split(set_clause, 1)[0]
    testcase.assertIn("MATCH (t:Task {id:$task_id})", before)
    testcase.assertIn("WHERE NOT (", before)
    testcase.assertIn("SET t.hermes_fence_check_epoch =", before)
    testcase.assertIn("WITH t", before)
    testcase.assertLess(before.index("SET t.hermes_fence_check_epoch ="),
                        before.index("WHERE NOT ("))
    for identity in SCOPE:
        testcase.assertIn(identity, before)


class FakeNeo:
    def __init__(self):
        self.session = MagicMock()
        self.session.run.return_value.single.return_value = None
        self.driver = MagicMock()
        self.driver.__enter__.return_value = self.session
        self.driver.__exit__.return_value = False

    def _session(self):
        return self.driver


class LegacySwarmClaimlessDenyTests(unittest.TestCase):
    def test_claimless_failure_is_scoped_before_mutation(self):
        q = query_for("fail_task")
        assert_guard_before_write(self, q, "SET t.status=$status")
        self.assertIn("AND NOT (coalesce($agent_id, '') STARTS WITH 'hermes-')", q)

    def test_claimless_lease_extension_is_scoped_before_mutation(self):
        q = query_for("set_task_lease")
        assert_guard_before_write(self, q, "SET t.lease_expires_at_ts=$expires")

    def test_denial_guard_mutations_are_caught(self):
        for fn, clause in (("fail_task", "SET t.status=$status"),
                           ("set_task_lease", "SET t.lease_expires_at_ts=$expires")):
            q = query_for(fn)
            for identity in SCOPE:
                with self.subTest(fn=fn, removed_identity=identity):
                    tampered = q.replace(identity, "false", 1)
                    with self.assertRaises(AssertionError):
                        assert_guard_before_write(self, tampered, clause)

    def test_callers_preserve_query_and_identity_parameters(self):
        neo = FakeNeo()
        self.assertIsNone(swarm_core.fail_task(neo, "synthetic-task", "hermes-local",
                                                "synthetic failure", retryable=True))
        self.assertEqual(neo.session.run.call_count, 1)
        query, values = neo.session.run.call_args.args
        self.assertEqual(query, query_for("fail_task"))
        self.assertEqual(values["agent_id"], "hermes-local")
        self.assertEqual(values["task_id"], "synthetic-task")
        neo = FakeNeo()
        self.assertIsNone(swarm_core.set_task_lease(neo, "synthetic-task", lease_seconds=5))
        query, values = neo.session.run.call_args.args
        self.assertEqual(query, query_for("set_task_lease"))
        self.assertEqual(values["task_id"], "synthetic-task")
        self.assertEqual(values["lease_seconds"], 5)

if __name__ == "__main__":
    unittest.main()
