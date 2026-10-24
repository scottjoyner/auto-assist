"""Pin claimless execution reconciler exclusion for Hermes-associated Task state.

The generic timeout/migration reconciler has no authoritative owner-bound
generation fence. This is only a fail-closed exclusion, not release proof.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[1] / "src/assistx/execution_control.py"
OWNER_FIELDS = (
    "coalesce(t.claimed_by, '') STARTS WITH 'hermes-'",
    "coalesce(t.target_agent_id, '') STARTS WITH 'hermes-'",
    "coalesce(t.completed_by, '') STARTS WITH 'hermes-'",
)


def reconcile_queries() -> list[str]:
    tree = ast.parse(FILE.read_text(encoding="utf-8"))
    run_queries = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "run":
            if node.args and isinstance(node.args[0], ast.Constant):
                q = node.args[0].value
                if isinstance(q, str):
                    run_queries.append(q)
    return run_queries


class GenericReconcileHOLDTests(unittest.TestCase):
    def test_timeout_requeue_locked_then_denied_for_hermes(self):
        matches = [q for q in reconcile_queries()
                   if "action:'preemption_timed_out'" in q]
        self.assertEqual(len(matches), 1)
        q = matches[0]
        self.assertIn("MATCH (t:Task {status:'PAUSING'})", q)
        self.assertIn("SET t.hermes_fence_check_epoch =", q)
        self.assertIn("WITH t", q)
        self.assertIn("WHERE t.status='PAUSING'", q)
        self.assertIn("AND NOT (", q)
        self.assertLess(q.index("SET t.hermes_fence_check_epoch ="),
                        q.index("WHERE t.status='PAUSING'"))
        self.assertLess(q.index("AND NOT ("), q.index("SET t.status=CASE"))
        for field in OWNER_FIELDS:
            self.assertIn(field, q)

    def test_migration_candidates_exclude_hermes(self):
        matches = [q for q in reconcile_queries()
                   if "MATCH (t:Task {status:'PAUSED'})" in q
                   and "LIMIT 100" in q]
        self.assertEqual(len(matches), 1)
        q = matches[0]
        self.assertIn("AND NOT (", q)
        for field in OWNER_FIELDS:
            self.assertIn(field, q)
        self.assertIn(
            "coalesce(t.migration_target_agent_id, '') STARTS WITH 'hermes-'",
            q,
        )

    def test_owner_predicate_mutations_fail(self):
        q = next(q for q in reconcile_queries() if "action:'preemption_timed_out'" in q)
        prefix = q.split("SET t.status=CASE", 1)[0]
        self.assertTrue(all(field in prefix for field in OWNER_FIELDS))
        for field in OWNER_FIELDS:
            with self.subTest(field=field):
                tampered = prefix.replace(field, "false", 1)
                self.assertFalse(all(required in tampered for required in OWNER_FIELDS))
        self.assertNotIn(
            "SET t.hermes_fence_check_epoch =",
            prefix.replace("SET t.hermes_fence_check_epoch =", "WITH t", 1),
        )
        candidate = next(q for q in reconcile_queries() if "MATCH (t:Task {status:'PAUSED'})" in q
                         and "LIMIT 100" in q)
        self.assertTrue(all(field in candidate for field in OWNER_FIELDS))
        for field in OWNER_FIELDS:
            with self.subTest(candidate=field):
                self.assertFalse(
                    all(required in candidate.replace(field, "false", 1)
                        for required in OWNER_FIELDS)
                )

if __name__ == "__main__":
    unittest.main()
