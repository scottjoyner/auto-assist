"""No-run synthetic policy and adversarial tests for RTK OSS intake."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "scripts/experiments/rtk_oss_shadow_retention.py"
spec = importlib.util.spec_from_file_location("rtk_oss_shadow_retention", SRC)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

MARKER = "FATAL_TRACE_HASH=8a593fac2bca17c4"
ORIGINAL = (
    b"running 3 tests\n"
    b"test a ... ok\ntest b ... ok\ntest c ... FAILED\n"
    b"thread panicked at src/retrieval.rs:87\n" + MARKER.encode() +
    b" signed-witness mismatch\n"
    b"test result: FAILED. 2 passed; 1 failed\n"
)
DROPPED = b"test result: FAILED. 2 passed; 1 failed\n"
PRESERVED = b"ERROR " + MARKER.encode() + b" gate denied\n"


class TestRTKRetention(unittest.TestCase):
    def review(self, raw=ORIGINAL, compact=PRESERVED, mode="log", status=1, markers=None):
        return gate.assess(raw, compact, mode=mode,
                           source_exit_status=status,
                           markers=markers or [MARKER])

    def test_preserved_failure_candidate_is_still_not_authoritative(self):
        r = self.review()
        self.assertTrue(r["research_summary_candidate"])
        self.assertEqual(r["preserved_markers"], 1)
        self.assertFalse(r["automatic_harness_rewrite_allowed"])
        self.assertFalse(r["compression_approved_for_claim_evidence"])
        self.assertFalse(r["production_dispatch_allowed"])
        self.assertTrue(r["source_output_remains_authoritative"])
        self.assertEqual(r["production_gate"], "HOLD")
        self.assertNotIn(MARKER, json.dumps(r))

    def test_missing_marker_denies_research_candidate(self):
        r = self.review(compact=DROPPED, mode="cargo-test")
        self.assertTrue(r["missing_required_marker"])
        self.assertEqual(r["preserved_markers"], 0)
        self.assertFalse(r["research_summary_candidate"])

    def test_compacted_retains_marker_but_not_failure_severity(self):
        r = self.review(compact=MARKER.encode() + b"\n")
        self.assertFalse(r["research_summary_candidate"])

    def test_git_log_forbidden_even_when_marker_present(self):
        self.assertFalse(self.review(mode="git-log")["research_summary_candidate"])

    def test_expanded_output_not_candidate(self):
        self.assertFalse(self.review(compact=ORIGINAL + b"\n")["research_summary_candidate"])

    def test_empty_compaction_not_candidate(self):
        self.assertFalse(self.review(compact=b"")["research_summary_candidate"])

    def test_source_exit_status_required(self):
        for val in (-1, 256, None, True, "1"):
            with self.subTest(value=val), self.assertRaises(gate.EvidenceRejected):
                self.review(status=val)

    def test_reject_unknown_filter(self):
        with self.assertRaises(gate.EvidenceRejected):
            self.review(mode="shell")

    def test_reject_missing_source_marker(self):
        with self.assertRaises(gate.EvidenceRejected):
            self.review(markers=["FATAL_TRACE_HASH=not-present"])

    def test_reject_tiny_marker(self):
        with self.assertRaises(gate.EvidenceRejected):
            self.review(markers=["x"])

    def test_reject_control_character_marker(self):
        for x in ("FATAL\nMARKER", "FATAL\rMARKER", "FATAL\0MARKER"):
            with self.subTest(x=x), self.assertRaises(gate.EvidenceRejected):
                self.review(markers=[x])

    def test_duplicate_marker_rejected(self):
        with self.assertRaises(gate.EvidenceRejected):
            self.review(markers=[MARKER, MARKER])

    def test_missing_marker_list_rejected(self):
        with self.assertRaises(gate.EvidenceRejected):
            gate.assess(ORIGINAL, PRESERVED, mode="log",
                        source_exit_status=1, markers=[])

    def test_oversize_or_empty_source_rejected(self):
        for payload in (b"", b"A" * (gate.MAX_BYTES + 1)):
            with self.subTest(length=len(payload)), self.assertRaises(gate.EvidenceRejected):
                self.review(raw=payload)

    def test_compacted_output_cannot_exceed_cap(self):
        with self.assertRaises(gate.EvidenceRejected):
            self.review(compact=b"B" * (gate.MAX_BYTES + 1))

    def test_truncation_signal_is_exposed(self):
        r = self.review(compact=b"ERROR " + MARKER.encode() + b"\n[+100 lines omitted]\n")
        self.assertTrue(r["truncation_signal_in_compacted_output"])

    def test_raw_and_compacted_digest_distinct(self):
        r = self.review()
        self.assertEqual(len(r["source_sha256"]), 64)
        self.assertNotEqual(r["source_sha256"], r["compacted_sha256"])

    def test_real_file_roundtrip_with_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            a, b = path/"original.txt", path/"compacted.txt"
            a.write_bytes(ORIGINAL)
            b.write_bytes(DROPPED)
            cmd = [sys.executable, str(SRC), "--source", str(a),
                   "--compacted", str(b), "--filter", "cargo-test",
                   "--source-exit-status", "1", "--required-marker", MARKER]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            self.assertEqual(res.returncode, 3)
            self.assertTrue(json.loads(res.stdout)["missing_required_marker"])
            self.assertNotIn(MARKER, res.stdout + res.stderr)

    def test_cli_fails_closed_on_absent_source(self):
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp)/"a", Path(temp)/"b"
            b.write_bytes(PRESERVED)
            res = subprocess.run([sys.executable, str(SRC), "--source", str(a),
                 "--compacted", str(b), "--filter", "log",
                 "--source-exit-status", "1", "--required-marker", MARKER],
                 capture_output=True, text=True, timeout=8)
            self.assertEqual(res.returncode, 2)
            self.assertNotIn("Traceback", res.stderr)

    def test_symlink_and_same_identity_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            a = path/"real"
            a.write_bytes(ORIGINAL)
            b = path/"link"
            b.symlink_to(a)
            with self.assertRaises(gate.EvidenceRejected):
                gate.read_regular(b)
            self.assertEqual(gate.read_regular(a)[0], ORIGINAL)

    def test_only_numeric_provenance_no_raw_evidence(self):
        result = self.review()
        self.assertNotIn("source_text", result)
        self.assertNotIn("marker_text", result)
        self.assertFalse(result["actual_prompt_tokens_measured"])


if __name__ == "__main__":
    unittest.main()
