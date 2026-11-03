"""Synthetic tests for aggregate-only ServiceEndpoint collision evidence."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from unittest import mock
import unittest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts" / "neo4j_service_endpoint_collision_readonly.py"
)
spec = importlib.util.spec_from_file_location("service_endpoint_collision", SCRIPT)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def clean() -> dict:
    return dict.fromkeys(checker.COLLISION_FIELDS, 0)


def collision() -> dict:
    return {
        "collision_groups": 1,
        "involved_nodes": 2,
        "excess_nodes": 1,
        "largest_group": 2,
        "divergent_property_groups": 1,
        "divergent_label_groups": 1,
    }


class ServiceEndpointCollisionContract(unittest.TestCase):
    def test_clean_aggregate_is_not_release_authorization(self):
        report = checker.report_from_aggregate(clean())
        self.assertEqual(
            report["status"], "PASS_NO_SERVICE_ENDPOINT_COLLISIONS_ONLY",
        )
        self.assertIs(report["graph_mutations_executed"], False)
        self.assertIs(report["production_release_authorized"], False)
        self.assertIs(report["automatic_collision_repair_authorized"], False)

    def test_live_collision_shape_is_hold(self):
        report = checker.report_from_aggregate(collision())
        self.assertEqual(
            report["status"], "HOLD_COLLIDING_SERVICE_ENDPOINT_IDENTITIES",
        )
        self.assertEqual(report["counts"]["excess_nodes"], 1)
        self.assertIs(report["identifier_values_logged"], False)
        self.assertIs(report["node_properties_or_records_logged"], False)

    def test_multiple_groups_with_consistent_cardinality(self):
        row = {
            "collision_groups": 2,
            "involved_nodes": 5,
            "excess_nodes": 3,
            "largest_group": 3,
            "divergent_property_groups": 2,
            "divergent_label_groups": 1,
        }
        self.assertEqual(
            checker.report_from_aggregate(row)["counts"]["largest_group"], 3,
        )

    def test_extra_or_missing_field_fails_closed(self):
        for case in ({**collision(), "endpoint_id": "value"}, {"collision_groups": 1}):
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    checker.validate_counts(case)

    def test_negative_or_boolean_counts_fail_closed(self):
        for key in checker.COLLISION_FIELDS:
            for invalid in (-1, True, "2", None, 1.25):
                with self.subTest(key=key, invalid=invalid):
                    row = collision()
                    row[key] = invalid
                    with self.assertRaises(ValueError):
                        checker.validate_counts(row)

    def test_zero_group_with_nonzero_detail_fails_closed(self):
        for key in checker.COLLISION_FIELDS:
            if key == "collision_groups":
                continue
            row = clean()
            row[key] = 1
            with self.subTest(key=key), self.assertRaises(ValueError):
                checker.validate_counts(row)

    def test_impossible_multi_group_counts_fail_closed(self):
        cases = (
            {**collision(), "involved_nodes": 1},
            {**collision(), "excess_nodes": 0},
            {**collision(), "largest_group": 1},
            {**collision(), "largest_group": 3},
            {**collision(), "divergent_property_groups": 2},
            {**collision(), "divergent_label_groups": 2},
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                checker.validate_counts(case)

    def test_cli_real_collision_returns_hold(self):
        output = io.StringIO()
        with mock.patch.object(checker, "read_live_aggregate", return_value=collision()):
            with redirect_stdout(output):
                code = checker.main()
        self.assertEqual(code, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "HOLD_COLLIDING_SERVICE_ENDPOINT_IDENTITIES")
        self.assertNotIn("endpoint_id", output.getvalue())

    def test_cli_clean_aggregate_still_cannot_authorize_release(self):
        output = io.StringIO()
        with mock.patch.object(checker, "read_live_aggregate", return_value=clean()):
            with redirect_stdout(output):
                code = checker.main()
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())["production_release_authorized"])

    def test_cli_backend_failure_redacts_sensitive_exception(self):
        output = io.StringIO()
        with mock.patch.object(
            checker, "read_live_aggregate",
            side_effect=RuntimeError("unprintable connection detail"),
        ):
            with redirect_stdout(output):
                code = checker.main()
        self.assertEqual(code, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "HOLD_COLLISION_WITNESS_UNVERIFIABLE")
        self.assertEqual(report["error_type"], "RuntimeError")
        self.assertNotIn("unprintable connection detail", output.getvalue())

    def test_cypher_is_read_only_and_returns_aggregates_not_identifiers(self):
        query = checker.COLLISION_QUERY.upper()
        self.assertIn("MATCH (E:SERVICEENDPOINT)", query)
        self.assertIn("RETURN COUNT(*) AS COLLISION_GROUPS", query)
        self.assertNotIn("RETURN IDENTITY", query)
        for operation in (" MERGE ", " SET ", " DELETE ", " CREATE ", " REMOVE ", " DROP "):
            with self.subTest(operation=operation):
                self.assertNotIn(operation, query)


if __name__ == "__main__":
    unittest.main()
