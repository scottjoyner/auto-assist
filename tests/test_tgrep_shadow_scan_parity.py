"""Synthetic negative tests for tgrep's non-authoritative scan-parity probe."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts/experiments/tgrep_shadow_scan_parity.py"
spec = importlib.util.spec_from_file_location("tgrep_shadow", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestTgrepShadow(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.base = Path(self.t.name)
        self.repo = self.base / "repo"
        self.index = self.base / "idx"
        self.repo.mkdir()
        self.index.mkdir()
        (self.repo / ".git").mkdir()
        self.bin = self.base / "bin"
        self.bin.write_bytes(b"synthetic-binary")
        self.pin = hashlib.sha256(self.bin.read_bytes()).hexdigest()

    def check(self, sets):
        with patch.object(mod, "call_search", side_effect=[(x, 2.0) for x in sets]), patch.object(
            mod, "verify_executable", return_value=None
        ):
            return mod.compare(self.repo, self.index, "TOKEN", self.bin, self.pin)

    def test_equal_results_never_authorize(self):
        r = self.check([{"a.py"}, {"a.py"}, {"a.py"}])
        self.assertTrue(r["index_matches_live_scan"])
        self.assertFalse(r["index_authoritative"])
        self.assertFalse(r["dispatch_allowed"])
        self.assertFalse(r["harness_change_allowed"])
        self.assertEqual(r["production_gate"], "HOLD")

    def test_stale_index_is_detected(self):
        r = self.check([{"a.py"}, set(), {"a.py"}])
        self.assertTrue(r["stale_or_divergent_index"])
        self.assertFalse(r["index_matches_live_scan"])
        self.assertTrue(r["scan_engines_agree"])

    def test_scan_engine_disagreement_is_not_silent(self):
        r = self.check([{"a.py"}, {"a.py"}, set()])
        self.assertFalse(r["verification_complete"])
        self.assertFalse(r["scan_engines_agree"])
        self.assertFalse(r["index_matches_live_scan"])

    def test_no_result_file_paths_ever_exported(self):
        sensitive = "customer-credential-file.py"
        r = self.check([{sensitive}, {sensitive}, {sensitive}])
        self.assertNotIn(sensitive, json.dumps(r))
        self.assertFalse(r["file_paths_exported"])

    def test_zero_matches_allowed(self):
        r = self.check([set(), set(), set()])
        self.assertEqual(r["scanner_file_matches"], 0)
        self.assertTrue(r["verification_complete"])

    def test_reject_newline_literals(self):
        for pattern in ("", "a\nb", "a\rb", "a\0b", "x" * 97):
            with self.subTest(pattern=pattern), self.assertRaises(mod.ProbeRejected):
                mod.compare(self.repo, self.index, pattern, self.bin, self.pin)

    def test_reject_nonrepo_root(self):
        other = self.base / "ordinary"
        other.mkdir()
        with self.assertRaises(mod.ProbeRejected):
            mod.compare(other, self.index, "needle", self.bin, self.pin)

    def test_reject_index_inside_source(self):
        child = self.repo / "idx"
        child.mkdir()
        with self.assertRaises(mod.ProbeRejected):
            mod.compare(self.repo, child, "needle", self.bin, self.pin)

    def test_verify_binary_hash(self):
        mod.verify_executable(self.bin, self.pin)
        with self.assertRaises(mod.ProbeRejected):
            mod.verify_executable(self.bin, "0" * 64)

    def test_verify_binary_bad_pin(self):
        with self.assertRaises(mod.ProbeRejected):
            mod.verify_executable(self.bin, "not-a-digest")

    def test_parse_nul_terminated_paths(self):
        x = str(self.repo / "test.py").encode() + b"\0"
        self.assertEqual(mod.parse_paths(x, self.repo.resolve()), {"test.py"})

    def test_parse_path_with_newline(self):
        x = str(self.repo / "some\nname.py").encode() + b"\0"
        self.assertEqual(mod.parse_paths(x, self.repo.resolve()), {"some\nname.py"})

    def test_parse_outside_checkout_denied(self):
        x = str(self.base / "outside.py").encode() + b"\0"
        with self.assertRaises(mod.ProbeRejected):
            mod.parse_paths(x, self.repo.resolve())

    def test_parse_unterminated_denied(self):
        with self.assertRaises(mod.ProbeRejected):
            mod.parse_paths(b"/tmp/secret", self.repo.resolve())

    def test_parse_duplicates_denied(self):
        x = str(self.repo / "a.py").encode() + b"\0"
        with self.assertRaises(mod.ProbeRejected):
            mod.parse_paths(x * 2, self.repo.resolve())

    def test_symlink_path_denied(self):
        link = self.repo / "linked.py"
        link.symlink_to(self.base / "outside.py")
        with self.assertRaises(mod.ProbeRejected):
            mod.parse_paths(str(link).encode() + b"\0", self.repo.resolve())

    def test_parse_oversize_denied(self):
        with self.assertRaises(mod.ProbeRejected):
            mod.parse_paths(b"x" * (mod.MAX_MATCH_BYTES + 1), self.repo.resolve())

    def test_command_rejects_nonzero_subprocess(self):
        fake = self.repo / "a.py"
        fake.write_text("x")
        with patch.object(mod.subprocess, "run") as run:
            run.return_value.returncode = 3
            run.return_value.stdout = b""
            with self.assertRaises(mod.ProbeRejected):
                mod.call_search(["rg"], self.repo)


if __name__ == "__main__":
    unittest.main()
