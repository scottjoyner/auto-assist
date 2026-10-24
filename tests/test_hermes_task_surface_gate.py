"""Offline strict source-drift gate regressions. No privileged operations."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import hermes_task_surface_gate as gate


class SourceSurfaceGateTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.path.joinpath("module.py").write_text(
            "def owner(tx, query):\n"
            "    tx.run(\"MATCH (t:Task) SET t.status='READY'\")\n"
            "    tx.run(query)\n"
        )

    def scan(self):
        return gate.surface(self.path)

    def test_current_repo_pinned_baseline_is_hold(self):
        snapshot=json.loads(gate.BASELINE.read_text())
        current=gate.surface(gate.ROOT)
        report=gate.validate(current,snapshot)
        self.assertTrue(report["surface_unchanged"])
        self.assertEqual(report["decision"],"HOLD")
        self.assertFalse(report["source_inventory_complete"])
        self.assertFalse(report["all_writers_generation_fenced"])
        self.assertFalse(report["production_dispatch_authorized"])
        self.assertEqual(current["static_task_writer_count"],68)
        self.assertEqual(current["unresolved_dynamic_query_count"],65)

    def test_fixture_detects_new_mutating_writer(self):
        expected=self.scan()
        self.path.joinpath("other.py").write_text(
            "def imported_writer(tx):\n"
            "    tx.run(\"MATCH(t:Task) SET t.claimed_by='legacy'\")\n"
        )
        with self.assertRaisesRegex(ValueError,"task_mutation_surface_drift"):
            gate.validate(self.scan(),expected)

    def test_fixture_detects_modified_static_query_even_when_count_stays_equal(self):
        expected=self.scan()
        src=self.path.joinpath("module.py")
        src.write_text(src.read_text().replace("t.status='READY'","t.status='DONE'"))
        actual=self.scan()
        self.assertEqual(actual["static_task_writer_count"],
                         expected["static_task_writer_count"])
        with self.assertRaisesRegex(ValueError,"task_mutation_surface_drift"):
            gate.validate(actual,expected)

    def test_fixture_detects_new_dynamic_query_call(self):
        expected=self.scan()
        src=self.path.joinpath("module.py")
        src.write_text(src.read_text()+"def extra(tx, q):\n    tx.run(q)\n")
        with self.assertRaisesRegex(ValueError,"task_mutation_surface_drift"):
            gate.validate(self.scan(),expected)

    def test_fixture_detects_changed_query_construction_around_dynamic_run(self):
        src=self.path.joinpath("module.py")
        src.write_text(
            "def owner(tx, name):\n"
            "    sql = f\"MATCH (t:Task) SET t.status='{name}'\"\n"
            "    tx.run(sql)\n"
        )
        original=self.scan()
        self.assertEqual(original["unresolved_dynamic_query_count"],1)
        src.write_text(src.read_text().replace("SET t.status","SET t.claimed_by"))
        updated=self.scan()
        self.assertEqual(updated["unresolved_dynamic_query_count"],1)
        with self.assertRaisesRegex(ValueError,"task_mutation_surface_drift"):
            gate.validate(updated,original)

    def test_irrelevant_source_comments_and_line_offsets_stay_stable(self):
        before=self.scan()
        src=self.path.joinpath("module.py")
        src.write_text("# not a mutation\n\n"+src.read_text())
        gate.validate(self.scan(),before)

    def test_removed_writer_denied(self):
        expected=self.scan()
        self.path.joinpath("module.py").write_text(
            "def owner(tx, query):\n    tx.run(query)\n"
        )
        with self.assertRaisesRegex(ValueError,"task_mutation_surface_drift"):
            gate.validate(self.scan(),expected)

    def test_tampered_release_flag_and_missing_entries_denied(self):
        actual=self.scan()
        unauthorized=copy.deepcopy(actual)
        unauthorized["production_dispatch_authorized"]=True
        with self.assertRaisesRegex(ValueError,"baseline_authority_claim"):
            gate.validate(actual,unauthorized)
        unauthorized=copy.deepcopy(actual)
        unauthorized["entries"].pop()
        with self.assertRaisesRegex(ValueError,"task_mutation_surface_drift"):
            gate.validate(actual,unauthorized)
        unauthorized=copy.deepcopy(actual)
        unauthorized.pop("source_inventory_complete")
        with self.assertRaisesRegex(ValueError,"invalid_baseline_schema"):
            gate.validate(actual,unauthorized)

    def test_source_symlink_and_invalid_code_fail_closed(self):
        path=self.path/"linked.py"
        path.symlink_to(self.path/"module.py")
        with self.assertRaisesRegex(ValueError,"unsafe_source_path"):
            self.scan()
        path.unlink()
        self.path.joinpath("module.py").write_text("def invalid(:\n")
        with self.assertRaisesRegex(ValueError,"unparseable_source_file"):
            self.scan()

    def test_no_sql_text_or_secret_is_echoed_in_baseline(self):
        self.path.joinpath("module.py").write_text(
            "def h(tx,secret):\n"
            "    tx.run(\"MATCH (t:Task) SET t.title='canary-secret-text'\")\n"
        )
        result=self.scan()
        encoded=json.dumps(result)
        self.assertNotIn("canary-secret-text",encoded)
        self.assertNotIn("MATCH (t:Task)",encoded)
        self.assertEqual(result["decision"],"HOLD")
        self.assertFalse(result["source_inventory_complete"])


if __name__=="__main__":
    unittest.main()
