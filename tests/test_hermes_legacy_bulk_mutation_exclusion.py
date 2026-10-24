"""Fail-closed checks for legacy bulk Task reset and deletion paths.

Static guard check only: this does not prove generation fencing, lineage
completeness, database isolation, or safe production deployment.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

SRC = Path(__file__).resolve().parents[1] / "src/assistx"
CLAIM_FIELDS = (
    "coalesce(t.claimed_by, '') STARTS WITH 'hermes-'",
    "coalesce(t.target_agent_id, '') STARTS WITH 'hermes-'",
    "coalesce(t.completed_by, '') STARTS WITH 'hermes-'",
)


def _cypher_statements(module: str) -> list[str]:
    source = (SRC / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "run"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]


def _guarded_before_mutation(statement: str, mutation: str) -> bool:
    if statement.count(mutation) != 1:
        return False
    before, _ = statement.split(mutation, 1)
    return (
        "MATCH (t:Task)" in before
        and "AND NOT (" in before
        and all(field in before for field in CLAIM_FIELDS)
    )


class LegacyBulkMutationGuardTests(unittest.TestCase):
    def test_maintenance_reapers_both_exclude_hermes_before_reset(self):
        statements = [
            q for q in _cypher_statements("maintenance.py")
            if "SET t.status = 'READY'" in q
            and "RETURN count(t) AS reaped" in q
        ]
        self.assertEqual(len(statements), 2)
        for query in statements:
            with self.subTest(reset=query.count("passive_claim")):
                self.assertTrue(
                    _guarded_before_mutation(query, "SET t.status = 'READY'"),
                    "a legacy maintenance reaper can reset a Hermes claim",
                )

    def test_terminal_gc_preserves_hermes_provenance(self):
        statements = [
            q for q in _cypher_statements("maintenance.py")
            if "DETACH DELETE t" in q and "MATCH (t:Task)" in q
        ]
        self.assertEqual(len(statements), 1)
        self.assertTrue(
            _guarded_before_mutation(statements[0], "DETACH DELETE t")
        )

    def test_swarm_bulk_lease_reaper_excludes_hermes(self):
        statements = [
            q for q in _cypher_statements("swarm_core.py")
            if "SET t.status='READY'" in q and "RETURN count(t) AS released" in q
        ]
        self.assertEqual(len(statements), 1)
        self.assertTrue(
            _guarded_before_mutation(statements[0], "SET t.status='READY'")
        )

    def test_guard_removal_or_partial_owner_scope_is_detected(self):
        statement = next(
            q for q in _cypher_statements("swarm_core.py")
            if "RETURN count(t) AS released" in q
        )
        self.assertTrue(_guarded_before_mutation(statement, "SET t.status='READY'"))
        for field in CLAIM_FIELDS:
            with self.subTest(field=field):
                self.assertFalse(
                    _guarded_before_mutation(
                        statement.replace(field, "true", 1),
                        "SET t.status='READY'",
                    )
                )
        self.assertFalse(
            _guarded_before_mutation(
                statement.replace("AND NOT (", "AND (", 1),
                "SET t.status='READY'",
            )
        )

if __name__ == "__main__":
    unittest.main()
