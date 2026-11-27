"""Synthetic fail-closed regression tests for ripwire advisory test-selection JSON."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/experiments/ripwire_shadow_test_gate.py"
spec = importlib.util.spec_from_file_location("ripwire_shadow_test_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SHA = "6868817518876de8fd170c6a8354daca273b6191"
SECRET = "SENSITIVE_COMMAND_LINE_OR_API_KEY_NEVER_PRINT"


class TestRipwireShadow(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "repo"
        (self.root / "tests").mkdir(parents=True)
        (self.root / ".git").mkdir()
        for f in ("test_a.py", "test_b.py", "test_c.py"):
            (self.root / "tests" / f).write_text("pass\n")

    def fixture(self):
        return {
            "changed": 1, "impacted": 86, "tests": 3,
            "untested": 40, "shown_tests": 3,
            "tests_capped": False, "untested_capped": True,
            "counts_floor": True,
            "graph_ambiguous": 21, "graph_unresolved": 12,
            "graph_unindexed": 7, "declined_calls": 13,
            "script_gates_unresolved_dynamic": 0,
            "script_gates_unmodelled": 0,
            "root": ".",
            "tests_to_run": [
                {"p": "tests/test_a.py", "hops": 1, "run": "echo " + SECRET},
                {"p": ["tests/test_b.py", "tests/test_c.py"], "n": 2, "run_unknown": True},
            ],
            "next": "evil-tool --api-key " + SECRET,
            "untested_blast_radius": [{"sym": SECRET, "p": "src/secret.py"}],
        }

    def evaluate(self, document=None, show=False):
        return gate.review(document or self.fixture(), self.root,
                           source_sha=SHA, include_local_paths=show)

    def test_non_authoritative_realistic_shape(self):
        r = self.evaluate()
        self.assertEqual(r["suggested_test_count"], 3)
        self.assertEqual(r["unknown_runner_test_count"], 2)
        self.assertTrue(r["selection_incomplete_or_uncertain"])
        self.assertFalse(r["coverage_complete"])
        self.assertFalse(r["dispatch_allowed"])
        self.assertFalse(r["test_coverage_independently_verified"])
        self.assertEqual(r["production_gate"], "HOLD")
        self.assertNotIn(SECRET, json.dumps(r))
        self.assertNotIn("verified_local_test_paths", r)

    def test_explicit_local_manifest_only(self):
        r = self.evaluate(show=True)
        self.assertEqual(r["verified_local_test_paths"],
                         ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"])

    def test_never_claims_coverage_even_with_no_warnings(self):
        d = self.fixture()
        for key in ("untested", "graph_ambiguous", "graph_unresolved",
                    "graph_unindexed", "declined_calls"):
            d[key] = 0
        d["untested_capped"] = False
        r = self.evaluate(d)
        self.assertFalse(r["coverage_complete"])
        self.assertFalse(r["test_coverage_independently_verified"])
        self.assertFalse(r["dispatch_allowed"])

    def test_input_count_disagreement_denied(self):
        d = self.fixture()
        d["shown_tests"] = 2
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_duplicate_path_denied(self):
        d = self.fixture()
        d["tests_to_run"][1]["p"][1] = "tests/test_a.py"
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_path_traversal_denied(self):
        for value in ("../secret.py", "tests/../../secret.py",
                      "/tmp/secret.py", "src/secret.py", "tests/test_a.txt"):
            d = self.fixture()
            d["tests_to_run"][0]["p"] = value
            with self.subTest(value=value), self.assertRaises(gate.UnsafeEvidence):
                self.evaluate(d)

    def test_symlink_denied(self):
        s = self.root / "tests" / "linked.py"
        s.symlink_to(self.root / "tests" / "test_a.py")
        d = self.fixture()
        d["tests_to_run"][0]["p"] = "tests/linked.py"
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_group_count_denied(self):
        d = self.fixture()
        d["tests_to_run"][1]["n"] = 3
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_unknown_runner_type_denied(self):
        d = self.fixture()
        d["tests_to_run"][1]["run_unknown"] = SECRET
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_missing_count_denied(self):
        d = self.fixture()
        del d["graph_unindexed"]
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_bool_integer_confusion_denied(self):
        d = self.fixture()
        d["tests"] = True
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_non_bool_truncation_denied(self):
        d = self.fixture()
        d["tests_capped"] = 1
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_no_floor_semantics_denied(self):
        d = self.fixture()
        d["counts_floor"] = False
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_omitted_selection_when_uncapped_denied(self):
        d = self.fixture()
        d["tests"] = 4
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate(d)

    def test_capped_partial_is_flagged(self):
        d = self.fixture()
        d["tests"] = 5
        d["tests_capped"] = True
        self.assertTrue(self.evaluate(d)["selection_incomplete_or_uncertain"])

    def test_unavailable_report_denied(self):
        with self.assertRaises(gate.UnsafeEvidence):
            self.evaluate({"root": ".", "tests_to_run": []})

    def test_invalid_sha_denied(self):
        with self.assertRaises(gate.UnsafeEvidence):
            gate.review(self.fixture(), self.root, source_sha="unknown")

    def test_cli_default_redacts_untrusted_string(self):
        with tempfile.TemporaryDirectory() as work:
            p = Path(work) / "report.json"
            p.write_text(json.dumps(self.fixture()))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--json-report", str(p),
                 "--source-root", str(self.root), "--source-sha", SHA],
                text=True, capture_output=True, timeout=8,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(SECRET, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["suggested_test_count"], 3)

    def test_cli_bad_json_no_echo(self):
        with tempfile.TemporaryDirectory() as work:
            p = Path(work) / "report.json"
            p.write_text('{"leak":"'+SECRET+'", "bad":NaN}')
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--json-report", str(p),
                 "--source-root", str(self.root), "--source-sha", SHA],
                text=True, capture_output=True, timeout=8,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_path_digest_stable_across_shuffled_recommendations(self):
        a = self.fixture()
        b = self.fixture()
        b["tests_to_run"].reverse()
        self.assertEqual(self.evaluate(a)["test_list_sha256"],
                         self.evaluate(b)["test_list_sha256"])


if __name__ == "__main__":
    unittest.main()
