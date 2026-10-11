"""Synthetic fail-closed tests for schema inventory vs Neo4j read-only metadata."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "neo4j_schema_drift_readonly.py"
spec = importlib.util.spec_from_file_location("assistx_neo4j_schema_audit", SCRIPT)
assert spec is not None and spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

SOURCE = """
class Neo4jClient:
    def ensure_schema(self):
        cypher = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Conversation) REQUIRE c.id IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (t:Task)   ON (t.status)",
        ]
"""
UNIQUENESS = {
    "entityType": "NODE", "type": "UNIQUENESS",
    "labelsOrTypes": ["Conversation"], "properties": ["id"],
}
RANGE = {
    "entityType": "NODE", "type": "RANGE",
    "labelsOrTypes": ["Task"], "properties": ["status"],
    "state": "ONLINE", "owningConstraint": None,
}


class SchemaAuditContract(unittest.TestCase):
    def test_parse_static_ddl_without_importing_neo4j_client(self):
        constraints, indexes, count = audit.declared_schema(SOURCE)
        self.assertEqual(constraints, {("Conversation", "id")})
        self.assertEqual(indexes, {("Task", "status")})
        self.assertEqual(count, 2)

    def test_parse_exact_assistx_schema_source_when_present(self):
        source_path = Path(__file__).resolve().parents[1] / "src/assistx/neo4j_client.py"
        self.assertTrue(source_path.is_file())
        constraints, indexes, n = audit.declared_schema(source_path.read_text())
        self.assertGreaterEqual(n, 100)
        self.assertEqual(len(constraints) + len(indexes), n)
        self.assertIn(("ServiceEndpoint", "endpoint_id"), constraints)

    def test_matches_complete_online_schema_without_release_authority(self):
        result = audit.audit(SOURCE, [UNIQUENESS], [RANGE])
        self.assertEqual(result["status"], "PASS_DECLARED_SCHEMA_METADATA_PRESENT")
        self.assertTrue(result["all_obligations_satisfied"])
        self.assertNotIn("missing_constraints", result)

    def test_one_missing_constraint_fails_closed(self):
        result = audit.audit(SOURCE, [], [RANGE])
        self.assertEqual(result["status"], "HOLD_DECLARED_SCHEMA_MISSING_OR_OFFLINE")
        self.assertEqual(result["missing_unique_constraints_count"], 1)
        self.assertEqual(result["matched_online_indexes"], 1)

    def test_missing_index_fails_closed(self):
        result = audit.audit(SOURCE, [UNIQUENESS], [])
        self.assertFalse(result["all_obligations_satisfied"])
        self.assertEqual(result["missing_or_offline_indexes_count"], 1)

    def test_offline_index_is_not_counted_as_present(self):
        result = audit.audit(SOURCE, [UNIQUENESS], [{**RANGE, "state": "POPULATING"}])
        self.assertEqual(result["offline_declared_indexes_count"], 1)
        self.assertEqual(result["matched_online_indexes"], 0)
        self.assertFalse(result["all_obligations_satisfied"])

    def test_constraint_owned_range_index_cannot_replace_standalone_index(self):
        result = audit.audit(
            SOURCE, [UNIQUENESS],
            [{**RANGE, "owningConstraint": "constraint-owned"}],
        )
        self.assertFalse(result["all_obligations_satisfied"])
        self.assertEqual(result["missing_or_offline_indexes_count"], 1)

    def test_extra_unrelated_schema_does_not_hide_missing(self):
        other_constraint = {
            **UNIQUENESS, "labelsOrTypes": ["KgNode"],
        }
        result = audit.audit(SOURCE, [other_constraint], [RANGE])
        self.assertEqual(result["missing_unique_constraints_count"], 1)

    def test_missing_names_only_emitted_with_opt_in(self):
        result = audit.audit(SOURCE, [], [], include_missing_names=True)
        self.assertEqual(result["missing_constraints"], [
            {"node_label": "Conversation", "property": "id"},
        ])
        self.assertEqual(result["missing_or_offline_indexes"], [
            {"node_label": "Task", "property": "status"},
        ])

    def test_node_schema_not_satisfied_by_relationship_metadata(self):
        result = audit.audit(
            SOURCE,
            [{**UNIQUENESS, "entityType": "RELATIONSHIP"}],
            [{**RANGE, "entityType": "RELATIONSHIP"}],
        )
        self.assertEqual(result["missing_unique_constraints_count"], 1)
        self.assertEqual(result["missing_or_offline_indexes_count"], 1)

    def test_dynamic_or_unknown_ddl_fails_closed(self):
        variants = (
            SOURCE.replace("cypher = [", "cypher = variable + ["),
            SOURCE.replace("CREATE INDEX IF NOT EXISTS", "DROP INDEX"),
            SOURCE.replace(
                '"CREATE INDEX IF NOT EXISTS FOR (t:Task)   ON (t.status)",',
                '"CREATE INDEX IF NOT EXISTS FOR (t:Task) ON (t.status)",'
                ', "CREATE INDEX IF NOT EXISTS FOR (t:Task) ON (t.status)",',
            ),
        )
        for variant in variants:
            with self.subTest(variant=variant[-70:]):
                with self.assertRaises(audit.IncompleteSchemaDeclaration):
                    audit.declared_schema(variant)

    def test_missing_neo4j_client_or_method_fails_closed(self):
        for source in ("class Other: pass", "class Neo4jClient: pass"):
            with self.assertRaises(audit.IncompleteSchemaDeclaration):
                audit.declared_schema(source)

    def test_cli_metadata_unavailable_holds_with_sanitized_error(self):
        output = io.StringIO()
        with (
            mock.patch.object(audit, "read_live_schema", side_effect=RuntimeError("SIMULATED_INTERNAL_DETAIL")),
            mock.patch.object(Path, "read_text", return_value=SOURCE),
            redirect_stdout(output),
        ):
            status = audit.main(["--source", str(SCRIPT)])
        self.assertEqual(status, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "HOLD_UNVERIFIABLE_SOURCE_OR_METADATA")
        self.assertEqual(report["error_type"], "RuntimeError")
        self.assertNotIn("SIMULATED_INTERNAL_DETAIL", output.getvalue())
        self.assertIs(report["production_release_authorized"], False)

    def test_cli_returns_hold_without_neo4j_connection_when_source_invalid(self):
        with mock.patch.object(audit, "read_live_schema") as db:
            output = io.StringIO()
            with mock.patch.object(Path, "read_text", return_value="not a python function"), redirect_stdout(output):
                status = audit.main(["--source", str(SCRIPT)])
        self.assertEqual(status, 2)
        self.assertFalse(db.called)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "HOLD_UNVERIFIABLE_SOURCE_OR_METADATA")
        self.assertFalse(report["production_release_authorized"])
        self.assertFalse(report["graph_ddl_or_write_executed"])


if __name__ == "__main__":
    unittest.main()
